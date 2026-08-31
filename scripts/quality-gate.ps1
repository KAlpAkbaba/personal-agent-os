# quality-gate.ps1 — deterministic quality gate for the Personal Agent OS monorepo.
#
# Modes:
#   -Fast : required-file + secret-hygiene checks, ruff lint, API unit tests.
#   full  : fast checks + dev stack up + alembic upgrade + integration tests.
#
# Windows PowerShell 5.1 compatible. Never relies on PATH for external tools.
# Exit code is nonzero if any step fails.

# -E2E additionally runs the M1 device end-to-end test (opens/closes Notepad
# in the interactive session; not suitable for headless CI).
param([switch]$Fast, [switch]$E2E)

# "Continue", not "Stop": docker compose, alembic and next write progress to
# stderr; under output redirection PS 5.1 would turn those lines into
# terminating NativeCommandError failures. Steps fail via Assert-ExitCode and
# explicit throws instead.
$ErrorActionPreference = "Continue"
$repoRoot = Split-Path -Parent $PSScriptRoot
$apiRoot = Join-Path $repoRoot "services\api"

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

$uv = Resolve-Tool "uv" @(
  "%USERPROFILE%\.local\bin\uv.exe",
  "%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe"
)
$powershell5 = Resolve-Tool "powershell" @("C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")

$results = New-Object System.Collections.ArrayList
$failed = $false

function Invoke-Step {
  param([string]$Name, [scriptblock]$Action)
  Write-Host ""
  Write-Host "=== $Name ===" -ForegroundColor Cyan
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $ok = $false
  try {
    & $Action
    $ok = $true
  } catch {
    Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
  }
  $sw.Stop()
  [void]$script:results.Add([pscustomobject]@{
    Step = $Name
    Result = $(if ($ok) { "PASS" } else { "FAIL" })
    Seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1)
  })
  if (-not $ok) { $script:failed = $true }
}

function Assert-ExitCode {
  param([string]$What)
  if ($LASTEXITCODE -ne 0) { throw "$What exited with code $LASTEXITCODE" }
}

# ---------------------------------------------------------------- fast checks

Invoke-Step "Required files" {
  $required = @(
    "PROJECT_CONSTITUTION.md",
    "docs/MASTER_SPEC.md",
    "docs/ARCHITECTURE.md",
    "docs/ACCEPTANCE_TESTS.md",
    "state/BUILD_STATE.json",
    ".env.example",
    "infra/docker/docker-compose.dev.yml",
    "services/api/pyproject.toml",
    "services/api/uv.lock"
  )
  foreach ($path in $required) {
    if (-not (Test-Path (Join-Path $repoRoot $path))) {
      throw "Missing required file: $path"
    }
  }
  Write-Host "All required files present."
}

Invoke-Step "Secret hygiene" {
  # .env must never be tracked; probe for obvious committed secrets.
  $git = Resolve-Tool "git" @("C:\Program Files\Git\cmd\git.exe")
  if (-not $git) { throw "git not found" }
  Push-Location $repoRoot
  try {
    $tracked = & $git ls-files
    Assert-ExitCode "git ls-files"
    $envTracked = $tracked | Where-Object { $_ -match "(^|/)\.env$" -or $_ -match "(^|/)\.env\.(?!example)" }
    if ($envTracked) { throw ".env-style file is tracked: $($envTracked -join ', ')" }
    $keyFiles = $tracked | Where-Object { $_ -match "\.(pem|pfx|p12|key)$" }
    if ($keyFiles) { throw "Key material is tracked: $($keyFiles -join ', ')" }

    # Content scan: real token/key patterns pasted into tracked files
    # (security review M0, finding #1). git grep exits 1 when nothing matches.
    $secretPattern = "AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{24,}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    $hits = & $git grep -nE $secretPattern -- ":!*.example" ":!*.lock"
    if ($LASTEXITCODE -eq 0 -and $hits) {
      throw "Potential secret content in tracked files:`n$($hits -join "`n")"
    }
    if ($LASTEXITCODE -gt 1) { throw "git grep secret scan failed with code $LASTEXITCODE" }
  } finally {
    Pop-Location
  }
  Write-Host "No tracked .env/key files and no secret-pattern content."
}

Invoke-Step "API lint (ruff)" {
  if (-not $uv) { throw "uv not found" }
  Push-Location $apiRoot
  try {
    & $uv run ruff check .
    Assert-ExitCode "ruff"
  } finally { Pop-Location }
}

Invoke-Step "API unit tests" {
  if (-not $uv) { throw "uv not found" }
  Push-Location $apiRoot
  try {
    & $uv run pytest tests/unit -q
    Assert-ExitCode "pytest (unit)"
  } finally { Pop-Location }
}

# ---------------------------------------------------------------- full checks

if (-not $Fast) {
  Invoke-Step "Dev stack up (docker compose)" {
    if (-not $powershell5) { throw "powershell.exe not found" }
    & $powershell5 -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot "scripts\dev-up.ps1")
    Assert-ExitCode "dev-up.ps1"
  }

  Invoke-Step "Alembic upgrade head" {
    Push-Location $apiRoot
    try {
      & $uv run alembic upgrade head
      Assert-ExitCode "alembic upgrade"
    } finally { Pop-Location }
  }

  Invoke-Step "API integration tests" {
    Push-Location $apiRoot
    try {
      & $uv run pytest tests/integration -q -m integration
      Assert-ExitCode "pytest (integration)"
    } finally { Pop-Location }
  }

  Invoke-Step "Windows agent build + tests" {
    $dotnet = $null
    foreach ($cand in @("$env:LOCALAPPDATA\Microsoft\dotnet\dotnet.exe", "C:\Program Files\dotnet\dotnet.exe")) {
      if (Test-Path $cand) {
        $sdks = & $cand --list-sdks 2>$null
        if ($LASTEXITCODE -eq 0 -and $sdks) { $dotnet = $cand; break }
      }
    }
    if (-not $dotnet) { throw "dotnet SDK not found" }
    $env:DOTNET_ROOT = Split-Path -Parent $dotnet
    Push-Location (Join-Path $repoRoot "devices\windows-agent")
    try {
      & $dotnet build PagentOS.WindowsAgent.sln --nologo -v q
      Assert-ExitCode "dotnet build"
      & $dotnet test PagentOS.WindowsAgent.sln --nologo --no-build -v q
      Assert-ExitCode "dotnet test"
    } finally { Pop-Location }
  }

  if ($E2E) {
    Invoke-Step "M1 device E2E (Notepad)" {
      & $powershell5 -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot "scripts\e2e-m1-device.ps1") -SkipBuild
      Assert-ExitCode "e2e-m1-device.ps1"
    }
  }

  Invoke-Step "Web shell build" {
    $pnpm = Resolve-Tool "pnpm" @("%APPDATA%\npm\pnpm.cmd", "%LOCALAPPDATA%\pnpm\pnpm.exe")
    if (-not $pnpm) { throw "pnpm not found" }
    Push-Location $repoRoot
    try {
      & $pnpm install --frozen-lockfile
      Assert-ExitCode "pnpm install"
      & $pnpm --dir apps\web build
      Assert-ExitCode "pnpm build (apps/web)"
    } finally { Pop-Location }
  }
}

# -------------------------------------------------------------------- summary

Write-Host ""
Write-Host "=== Quality gate summary ===" -ForegroundColor Cyan
$results | Format-Table -AutoSize | Out-String | Write-Host

if ($failed) {
  Write-Host "QUALITY GATE: FAIL" -ForegroundColor Red
  exit 1
}
Write-Host "QUALITY GATE: PASS" -ForegroundColor Green
exit 0
