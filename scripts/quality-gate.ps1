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
    # Failures report file:line ONLY - a scanner that echoes what it matched would be the
    # leak it exists to prevent.
    $secretPattern = "AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{24,}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    $hits = & $git grep -nE $secretPattern -- ":!*.example" ":!*.lock"
    if ($LASTEXITCODE -eq 0 -and $hits) {
      $locations = @($hits | ForEach-Object { (($_ -split ":", 3)[0..1]) -join ":" })
      throw "Potential secret content in tracked files (values not shown): $($locations -join ', ')"
    }
    if ($LASTEXITCODE -gt 1) { throw "git grep secret scan failed with code $LASTEXITCODE" }

    # PagentOS-minted credentials and session tokens: a REAL mint is pagentos_ok_ or
    # pagentos_st_ followed by 43 urlsafe-base64 characters (token_urlsafe(32)); the 40+
    # threshold catches it with margin. The rule is SHAPE-based with no allowlist and no
    # exclusions: synthetic fixtures are legitimate only while off-shape (short, or built
    # from words - the 33-char marker in machine-readable.tests.ps1 is the pattern to
    # follow), because a fixture that perfectly imitates a production credential is
    # indistinguishable from a leak, which makes it one. History classification 2026-09-01:
    # only prefixes and off-shape fixtures have ever been committed.
    $pagentosPattern = "pagentos_(ok|st)_[A-Za-z0-9_-]{40,}"
    $hits = & $git grep -nE $pagentosPattern
    if ($LASTEXITCODE -eq 0 -and $hits) {
      $locations = @($hits | ForEach-Object { (($_ -split ":", 3)[0..1]) -join ":" })
      throw "Production-shaped PagentOS credential in tracked files (values not shown): $($locations -join ', '). If this is a fixture, make it off-shape; the rule has no allowlist."
    }
    if ($LASTEXITCODE -gt 1) { throw "git grep pagentos credential scan failed with code $LASTEXITCODE" }
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

# B08 (2026-09-13): the other Python services' ruff runs in CI and used to run here only in
# the FULL gate, so every -Fast pass said "clean" about a tree CI was about to reject. It cost
# a red CI job on the recovery supervisor for a single long line. Lint is seconds; their test
# suites stay in the full gate where they belong.
Invoke-Step "Other services lint (ruff)" {
  if (-not $uv) { throw "uv not found" }
  foreach ($svc in @("servicesecovery-supervisor", "servicesrowser")) {
    Push-Location (Join-Path $repoRoot $svc)
    try {
      & $uv run ruff check .
      Assert-ExitCode "ruff ($svc)"
    } finally { Pop-Location }
  }
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

  Invoke-Step "Script syntax (PowerShell 5.1)" {
    # A PowerShell 7-only construct is a parse error on the owner's 5.1 machine, so the script
    # dies on its first line. One such slip reached a credential-rotation script.
    $script = Join-Path $repoRoot "scripts\tests\script-syntax.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "script syntax tests"
  }

  Invoke-Step "Machine-readable child protocol" {
    # A rotation committed and lost its replacement credential because the child mixed a log
    # line into machine-readable stdout and the wrapper parsed leniently. These drive a real
    # child through every contamination shape and assert no error path quotes the secret.
    $script = Join-Path $repoRoot "scripts\tests\machine-readable.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "machine-readable protocol tests"
  }

  Invoke-Step "Installer invocation tests" {
    # The installer's native-tool invocation is a deterministic, offline check, and it earns
    # its place in the gate: a real install on the owner's machine failed at `sc create` with
    # ERROR_INVALID_COMMAND_LINE because PowerShell mangled an argument containing quotes.
    # These tests assert the exact argument shape and round-trip argv through a real child.
    $script = Join-Path $repoRoot "scripts\tests\installer-invocation.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "installer invocation tests"
  }

  Invoke-Step "Installer PS5.1 StrictMode tests" {
    # Windows PowerShell 5.1 cardinality: an elevated rerun died on `.Count` over a returned
    # empty collection, which unrolls to $null. These cover 0/1/many for every
    # collection-returning function and lint the pattern out of the installer scripts.
    $script = Join-Path $repoRoot "scripts\tests\installer-strictmode.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "installer strictmode tests"
  }

  Invoke-Step "Deployment transaction tests" {
    # A real deployment renamed the live service directory while the service ran from it and
    # left an asymmetric half-state. These drive the journaled engine through the incident
    # shape, every failure leg, and recovery from the exact partial state it left.
    $script = Join-Path $repoRoot "scripts\tests\installer-deploy.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "deployment transaction tests"
  }

  Invoke-Step "Installer ACL + recovery tests" {
    # Also deterministic and offline: builds real hardened trees in %TEMP%, reproduces the
    # empty-DACL state a previous install left on the owner's machine, and proves the
    # installer recovers from it without weakening anything.
    $script = Join-Path $repoRoot "scripts\tests\installer-acl.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "installer ACL tests"
  }

  Invoke-Step "Installer browser-worker provisioning tests (PS 5.1)" {
    # M13 (ADR-0050): the installer provisions the Browser Worker venv into the agent's
    # install tree, self-checks it in staging and after publish, and writes the companion's
    # worker configuration; these tests pin that transaction without touching an install.
    $script = Join-Path $repoRoot "scripts\tests\installer-browser.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "installer browser tests"
  }

  Invoke-Step "Installer evidence + engine wiring tests (PS 5.1)" {
    # 2026-09-03: the installer's inline swap failed under running processes and looked
    # successful; these pin the journaled engine as the only deploy path, the evidence
    # block, and the fail-loud M13 support assertion.
    $script = Join-Path $repoRoot "scripts\tests\installer-evidence.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "installer evidence tests"
  }

  Invoke-Step "Staged-update candidate + Cloud Core verification tests (PS 5.1)" {
    # 2026-09-08: the staged-update code (candidate manifest, Cloud Core heartbeat check)
    # had a test suite that NO GATE RAN. It passed against a device row it had invented,
    # while production returned a different shape, and a healthy 0.6.0 candidate was rolled
    # back. The suite is a gate now; the shape it uses is held by
    # services/api/tests/unit/test_device_identity_contract.py from the other side.
    $script = Join-Path $repoRoot "scripts\tests\agent-update.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "agent staged-update tests"
  }

  Invoke-Step "Recovery supervisor tests" {
    if (-not $uv) { throw "uv not found" }
    Push-Location (Join-Path $repoRoot "services\recovery-supervisor")
    try {
      & $uv run ruff check .
      Assert-ExitCode "ruff (supervisor)"
      & $uv run pytest -q
      Assert-ExitCode "pytest (supervisor)"
    } finally { Pop-Location }
  }

  Invoke-Step "Browser agent lint + tests" {
    if (-not $uv) { throw "uv not found" }
    Push-Location (Join-Path $repoRoot "services\browser")
    try {
      & $uv run ruff check .
      Assert-ExitCode "ruff (browser)"
      & $uv run pytest -q
      Assert-ExitCode "pytest (browser unit)"
      # Chromium is a one-time user-scope install; make the gate self-healing.
      & $uv run playwright install chromium
      Assert-ExitCode "playwright install chromium"
      & $uv run pytest -q -m browser
      Assert-ExitCode "pytest (browser e2e)"
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
      # Release too, because a LATER step judges it. `qualify-staged-update.ps1` takes
      # bin\Release as the candidate (falling back to Debug), so without this the gate
      # tests one binary and qualifies another - and a stale Release tree is qualified as
      # though it were the tree. Found 2026-09-11 when a new identity field read empty.
      & $dotnet build PagentOS.WindowsAgent.sln -c Release --nologo -v q
      Assert-ExitCode "dotnet build -c Release"
    } finally { Pop-Location }
  }

  Invoke-Step "Agent audit reader (PS5.1)" {
    # The cloud driver reported "no command row found" while real commands succeeded:
    # its verifier read the timestamp from `at`, a field the writer never emitted (`ts`).
    # These pin the reader to the real schema and to identity-based correlation.
    $script = Join-Path $repoRoot "scripts\tests\agent-audit.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "agent audit tests"
  }

  Invoke-Step "Owner explain harness (PS5.1)" {
    # 2026-09-04: the M16 harness started the web shell without waiting and then required a
    # session newer than its own start time; these pin readiness gating and correlation.
    $script = Join-Path $repoRoot "scripts\tests\owner-explain.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "owner explain harness tests"
  }

  Invoke-Step "Cloud secret shipping (PS5.1)" {
    # The owner's provider-credential path: DPAPI store -> Tailscale SSH stdin -> /opt/pagentos/.env.
    # Proven with a real native fake ssh (5.1 native-argument quoting byte for byte, value
    # only ever on stdin) and pinned to tr-TR for the Turkish-I case-folding bug that
    # refused every secret name containing an I on the owner's machine.
    $script = Join-Path $repoRoot "scripts\tests\cloud-secret.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "cloud secret tests"
  }

  Invoke-Step "Cloud Core release transaction (PS5.1 + Git Bash)" {
    # ADR-0042: the host ran a copied tree from the first deployment, so a "restart" after
    # installing a secret changed nothing. The release (git archive -> app.next -> validate
    # -> swap -> build -> migrate -> recreate ONLY the api -> verify -> rollback on failure)
    # is proven here with a real native fake ssh/scp and a fake docker under Git Bash.
    $script = Join-Path $repoRoot "scripts\tests\cloud-release.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "cloud release tests"
  }

  Invoke-Step "Cloud Core blue/green release (PS5.1 + Git Bash)" {
    # M18.4 (spec §6): the idle colour is brought up on the new sha, verified, switched to,
    # the old colour drained; rollback is the switch in reverse. Proven under a fake docker
    # that knows the two colours and the edge; the first real handoff is the next release.
    $script = Join-Path $repoRoot "scripts\tests\cloud-release-bluegreen.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "cloud release blue/green tests"
  }

  Invoke-Step "UTF-8 JSON decoding (PS5.1)" {
    # A real qualification record showed Turkish letters as mojibake: 5.1 decoded a
    # charset-less JSON body as Latin-1 while the database held correct UTF-8.
    $script = Join-Path $repoRoot "scripts\tests\utf8-json.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "utf8 json tests"
  }

  Invoke-Step "Config swap transaction (PS5.1)" {
    # A real broker switch died inside [IO.File]::Replace: PowerShell binds $null to a
    # [string] parameter as an EMPTY string, which .NET refuses as a path. These reproduce
    # it and prove the transactional replace: same-volume staging + real backup, stale
    # staging files, existing backups, validation before going live, rollback, idempotence.
    $script = Join-Path $repoRoot "scripts\tests\config-swap.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "config swap tests"
  }

  Invoke-Step "Provisioning + parameter collisions (PS5.1)" {
    # A real provisioning run applied four billable resources and then died assigning the
    # result over its own [switch]$Apply parameter - PowerShell variable names are
    # case-insensitive - so it looked like it had stopped before applying. These drive
    # provision.ps1 end to end through a fake tofu and lint every script for the class.
    $script = Join-Path $repoRoot "scripts\tests\provision.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "provisioning tests"
  }

  Invoke-Step "Identity restoration (PS5.1 + real key)" {
    # A real finalize run died calling ECDsa.ImportFromPem from Windows PowerShell 5.1,
    # whose .NET Framework does not have it. These run AFTER the agent build: the real
    # service exe performs a real loopback enrollment, and the `identity` verb must return
    # the enrolled public key byte-for-byte - the property broker-registration restore
    # depends on. Also proves the verb is load-only: asking never mints a key.
    $script = Join-Path $repoRoot "scripts\tests\identity-restore.tests.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "identity restoration tests"
  }

  Invoke-Step "Staged-update qualification (real candidate binary, sandbox engine)" {
    # Runs AFTER the agent build, like the identity restoration step: the REAL
    # DeviceService answers its own capabilities verb, the REAL journaled engine performs
    # the swap/commit/rollback in a sandbox, and the REAL Cloud Core verifier reads a
    # device row in the shape Cloud Core actually returns. Nothing here touches the live
    # install, the SCM, the running service or the owner's session.
    $script = Join-Path $repoRoot "scripts\qualify-staged-update.ps1"
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -File $script
    Assert-ExitCode "staged-update qualification"
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
