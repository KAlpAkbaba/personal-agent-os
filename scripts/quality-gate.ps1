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
#
# Test queue (the "test sirasi", scripts/lib/TeamTestSlots.ps1): before each HEAVY step the gate
# asks the machine's queue for the step's kinds (database / desktop / heavy), WAITS until
# ONAY - asking every -TestSlotPollSeconds, printing the BEKLE line once a minute - and holds
# the slot for that step only. The gate goes ahead of every waiting agent, never ahead of a
# run already going. Its wait is the WaitSeconds column of the summary. -NoTestSlots runs
# the gate as before, asking nothing. -TestSlotStore is the queue's folder (tests);
# -StepList <file.ps1> replaces the built-in steps with the file's own Invoke-Step calls
# (tests of the gate's step machinery: nothing real runs).
# -GateSerial runs every step one after another, as before the suite group (gate-faster);
# -GateMaxParallel sets the group's width (0 = the named default, GateSteps.ps1: 3).
# -OnlyStep <pattern,...> runs only the steps whose names match a pattern (-like); every other
# step keeps its row as SKIPPED. A slice of the gate for a run that must fit in a time limit;
# a slice is never "the gate passed" - only a run without -OnlyStep is.
param(
  [switch]$Fast,
  [switch]$E2E,
  [switch]$NoTestSlots,
  [string]$TestSlotStore = "",
  [int]$TestSlotPollSeconds = 20,
  [string]$StepList = "",
  [switch]$GateSerial,
  [int]$GateMaxParallel = 0,
  [string[]]$OnlyStep = @()
)

# "Continue", not "Stop": docker compose, alembic and next write progress to
# stderr; under output redirection PS 5.1 would turn those lines into
# terminating NativeCommandError failures. Steps fail via Assert-ExitCode and
# explicit throws instead.
$ErrorActionPreference = "Continue"
$repoRoot = Split-Path -Parent $PSScriptRoot
$apiRoot = Join-Path $repoRoot "services\api"
$gateClock = [System.Diagnostics.Stopwatch]::StartNew()

# gate-faster (team/plans/gate-faster-adr.md): the gate's own database and its suite group.
# Both libraries only define functions and settings; neither changes this script's preferences.
. (Join-Path $PSScriptRoot "lib\GateDatabase.ps1")
. (Join-Path $PSScriptRoot "lib\GateSteps.ps1")
$script:GateGroup = $null
$script:GateRecording = $null

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

# The library sets StrictMode for its own sake; this script was written without it.
. (Join-Path $repoRoot "scripts\lib\TeamTestSlots.ps1")
Set-StrictMode -Off
$script:SlotStore = if ($TestSlotStore) { $TestSlotStore } else { Get-TestSlotDefaultStore }
$script:SlotTask = "gate:" + (Split-Path -Leaf $repoRoot)

function Enter-GateTestSlot {
  # Asks the queue for $Kinds and waits for ONAY; returns the ticket (or $null) and the wait.
  # The queue is a courtesy between runs, not a gate step: a broken queue never fails the gate.
  param([string]$What, [string[]]$Kinds)
  $slot = [pscustomobject]@{ Ticket = $null; Waited = 0 }
  if (@($Kinds).Count -eq 0 -or $NoTestSlots) { return $slot }
  try {
    $grant = Wait-TestSlotGrant -Store $script:SlotStore -Kind $Kinds -Task $script:SlotTask -Role "gate" -What $What -PollSeconds $TestSlotPollSeconds
    $slot.Waited = $grant.WaitedSeconds
    [void](Start-TestSlotRun -Store $script:SlotStore -Ticket $grant.Ticket -HolderPid $PID)
    $slot.Ticket = $grant.Ticket
    Write-Host "TEST SIRASI: ONAY $($grant.Ticket) ($($Kinds -join ',')) after $($slot.Waited) s" -ForegroundColor DarkGray
  } catch {
    Write-Host "TEST SIRASI: the queue failed ($($_.Exception.Message)); the step runs without a slot" -ForegroundColor Yellow
  }
  return $slot
}

function Exit-GateTestSlot {
  param($Slot, [bool]$Ok)
  if (-not $Slot.Ticket) { return }
  try { Complete-TestSlotRun -Store $script:SlotStore -Ticket $Slot.Ticket -ExitCode $(if ($Ok) { "0" } else { "1" }) }
  catch { Write-Host "TEST SIRASI: release failed ($($_.Exception.Message)); the slot frees when the gate ends" -ForegroundColor Yellow }
}

function Invoke-Step {
  # -Kinds: the test-queue kinds this step needs (empty = a light step, no slot). A grouped
  # step's kinds are asked for once, for the whole group, by Complete-GateGroup.
  param([string]$Name, [scriptblock]$Action, [string[]]$Kinds = @())
  # -OnlyStep through -File arrives as ONE comma-joined string; a test that lifts this function
  # under StrictMode has no such variable at all (both: 2026-10-04).
  $only = @(Get-Variable -Name OnlyStep -ValueOnly -ErrorAction SilentlyContinue | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
  if ($only.Count -gt 0) {
    # A pattern that matches no step is named in the summary: a step name with a comma in it is
    # split by the list, so its pattern must stop before the comma ("Agent team cycle*").
    if ($null -eq (Get-Variable -Name OnlyStepUnmatched -Scope Script -ErrorAction SilentlyContinue)) {
      $script:OnlyStepUnmatched = New-Object System.Collections.ArrayList
      foreach ($p in $only) { [void]$script:OnlyStepUnmatched.Add($p) }
    }
    $hits = @($only | Where-Object { $Name -like $_ })
    foreach ($p in $hits) { $script:OnlyStepUnmatched.Remove($p) }
    if (-not $hits.Count) {
      # Inside the group a skipped step keeps its LISTED place among the group's rows.
      if ($null -ne $script:GateGroup) { [void]$script:GateGroup.Add([pscustomobject]@{ Name = $Name; Skipped = $true }); return }
      [void]$script:results.Add([pscustomobject]@{ Step = $Name; Result = "SKIPPED"; Seconds = 0; WaitSeconds = 0 })
      return
    }
  }
  if ($null -ne $script:GateGroup) {
    # Between Start-GateGroup and Complete-GateGroup a step is RECORDED, not run: its
    # Invoke-GateSuite gives the script, its Assert-ExitCode the words of its FAILED line, and
    # Complete-GateGroup runs it as a process of its own beside the others.
    # A step that throws while it is recorded (a missing tool) is a failed step in the words a
    # sequential run prints, reported by Complete-GateGroup in its place - never the end of the
    # gate without its summary (the inspector's finding, 2026-10-04).
    $script:GateRecording = [pscustomobject]@{ Script = ""; Lane = ""; What = "" }
    $thrown = ""
    try { & $Action | Out-Null } catch { $thrown = $_.Exception.Message } finally { $recorded = $script:GateRecording; $script:GateRecording = $null }
    if (-not $thrown -and $recorded.Script -and $recorded.What) {
      $step = New-GateStep -Name $Name -Script $recorded.Script -What $recorded.What -Lane $recorded.Lane
      $step | Add-Member -NotePropertyName Kinds -NotePropertyValue @($Kinds)
      [void]$script:GateGroup.Add($step)
      return
    }
    $why = if ($thrown) { $thrown } else { "a grouped step must call Invoke-GateSuite and Assert-ExitCode" }
    [void]$script:GateGroup.Add([pscustomobject]@{ Name = $Name; Failure = $why })
    return
  }
  Write-Host ""
  Write-Host "=== $Name ===" -ForegroundColor Cyan
  $waited = 0
  $slot = $null
  if (@($Kinds).Count -gt 0) {
    $slot = Enter-GateTestSlot -What $Name -Kinds $Kinds
    $waited = $slot.Waited
  }
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $ok = $false
  try {
    & $Action
    $ok = $true
  } catch {
    Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
  } finally {
    if ($slot) { Exit-GateTestSlot -Slot $slot -Ok $ok }
  }
  $sw.Stop()
  [void]$script:results.Add([pscustomobject]@{
    Step = $Name
    Result = $(if ($ok) { "PASS" } else { "FAIL" })
    Seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    WaitSeconds = $waited
  })
  if (-not $ok) { $script:failed = $true }
}

function Get-StepFailureText {
  # The words after "FAILED: " - one function, so a grouped step and a sequential one fail in
  # the same words (team-integrate and the lead read these lines).
  param([string]$What, $Code, [switch]$TimedOut, [int]$DeadlineSeconds = 0)
  if ($TimedOut) { return "$What $($script:GateStepTimeoutWord): did not end within $DeadlineSeconds s; killed with its process tree" }
  return "$What exited with code $Code"
}

function Assert-ExitCode {
  param([string]$What)
  if ($null -ne $script:GateRecording) { $script:GateRecording.What = $What; return }
  if ($LASTEXITCODE -ne 0) { throw (Get-StepFailureText -What $What -Code $LASTEXITCODE) }
}

function Invoke-GateSuite {
  # A PowerShell suite of the gate: run here, now (sequential, -GateSerial, or outside a group),
  # or - while Invoke-Step records a grouped step - remembered for the group. $Lane names what
  # the suite shares with others of the same lane; a lane runs one suite at a time.
  param([Parameter(Mandatory = $true)][string]$Path, [string]$Lane = "")
  if ($null -ne $script:GateRecording) {
    $script:GateRecording.Script = $Path
    $script:GateRecording.Lane = $Lane
    return
  }
  if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
  & $powershell5 -NoProfile -ExecutionPolicy Bypass -File $Path
}

function Start-GateGroup {
  # The Invoke-Step blocks from here to Complete-GateGroup run side by side (gate-faster):
  # recorded first, then started together. -GateSerial: they run one by one, as before.
  if ($GateSerial) { return }
  $script:GateGroup = New-Object System.Collections.ArrayList
}

function Complete-GateGroup {
  # Runs the recorded steps (scripts/lib/GateSteps.ps1), then reports each one as Invoke-Step
  # would have: its own "=== name ===" section with its whole log (the failed steps first),
  # its FAILED line in the same words, and its row of the table in the LISTED order.
  if ($null -eq $script:GateGroup) { return }
  $entries = @($script:GateGroup.ToArray())
  $script:GateGroup = $null
  if (@($entries).Count -eq 0) { return }
  # A step that failed while it was recorded is not run; it keeps its place in the table.
  # A step -OnlyStep left out is not run either; it keeps its place as SKIPPED.
  $refused = @($entries | Where-Object { $_.PSObject.Properties["Failure"] })
  $steps = @($entries | Where-Object { -not $_.PSObject.Properties["Failure"] -and -not $_.PSObject.Properties["Skipped"] })
  foreach ($f in $refused) {
    Write-Host ""
    Write-Host "=== $($f.Name) ===" -ForegroundColor Cyan
    Write-Host "FAILED: $($f.Failure)" -ForegroundColor Red
    $script:failed = $true
  }
  if (@($steps).Count -eq 0) {
    foreach ($e in $entries) { [void]$script:results.Add([pscustomobject]@{ Step = $e.Name; Result = $(if ($e.PSObject.Properties["Skipped"]) { "SKIPPED" } else { "FAIL" }); Seconds = 0; WaitSeconds = 0 }) }
    return
  }
  # The test queue: the kinds of every grouped step, asked for once and held for the group; the
  # wait is written on the group's first row, so the summary's total counts it once.
  $kinds = @($steps | Where-Object { $_.PSObject.Properties["Kinds"] } | ForEach-Object { $_.Kinds } | Where-Object { $_ } | Select-Object -Unique)
  $slot = Enter-GateTestSlot -What "suite group" -Kinds $kinds
  $waitRow = $slot.Waited
  $width = if ($GateMaxParallel -gt 0) { $GateMaxParallel } else { $script:GateStepMaxParallel }
  $lanes = @($steps | Where-Object { $_.Lane } | ForEach-Object { $_.Lane } | Select-Object -Unique)
  $logRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("pagentos-gate-steps-" + $PID + "-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
  Write-Host ""
  Write-Host ("suite group: {0} steps, at most {1} at once{2}" -f @($steps).Count, $width, $(if (@($lanes).Count) { "; one at a time in each lane: " + ($lanes -join ", ") } else { "" }))
  $clock = [System.Diagnostics.Stopwatch]::StartNew()
  $groupError = ""
  $ran = @()
  try { $ran = @(Invoke-GateStepGroup -Steps $steps -LogRoot $logRoot -MaxParallel $width -PowerShellPath $powershell5) }
  catch { $groupError = $_.Exception.Message }
  finally { Exit-GateTestSlot -Slot $slot -Ok ((-not $groupError) -and (Test-GateStepGroupPassed -Results $ran)) }
  $clock.Stop()
  if ($groupError) {
    foreach ($s in $steps) {
      Write-Host ""
      Write-Host "=== $($s.Name) ===" -ForegroundColor Cyan
      Write-Host "FAILED: $($s.What): the suite group did not run: $groupError" -ForegroundColor Red
    }
    foreach ($e in $entries) {
      if ($e.PSObject.Properties["Skipped"]) { [void]$script:results.Add([pscustomobject]@{ Step = $e.Name; Result = "SKIPPED"; Seconds = 0; WaitSeconds = 0 }); continue }
      [void]$script:results.Add([pscustomobject]@{ Step = $e.Name; Result = "FAIL"; Seconds = 0; WaitSeconds = $waitRow }); $waitRow = 0
    }
    $script:failed = $true
    Remove-Item -LiteralPath $logRoot -Recurse -Force -ErrorAction SilentlyContinue
    return
  }
  $own = ($ran | Measure-Object -Property Seconds -Sum).Sum
  Write-Host ("suite group: {0} s wall; the steps' own seconds add up to {1} s" -f [math]::Round($clock.Elapsed.TotalSeconds), [math]::Round($own))
  $encoding = [Console]::OutputEncoding
  foreach ($r in (@($ran | Where-Object { $_.Outcome -ne "PASS" }) + @($ran | Where-Object { $_.Outcome -eq "PASS" }))) {
    Write-Host ""
    Write-Host "=== $($r.Name) ===" -ForegroundColor Cyan
    $out = if (Test-Path -LiteralPath $r.StdoutPath) { [System.IO.File]::ReadAllText($r.StdoutPath, $encoding) } else { "" }
    $err = if (Test-Path -LiteralPath $r.StderrPath) { [System.IO.File]::ReadAllText($r.StderrPath, $encoding) } else { "" }
    if ($out.Trim()) { Write-Host $out.TrimEnd() }
    if ($err.Trim()) { Write-Host "--- stderr ---"; Write-Host $err.TrimEnd() }
    if ($r.Outcome -ne "PASS") {
      $text = Get-StepFailureText -What $r.What -Code $r.ExitCode -TimedOut:($r.Outcome -eq $script:GateStepTimeoutWord) -DeadlineSeconds $r.Deadline
      Write-Host "FAILED: $text" -ForegroundColor Red
    }
  }
  # The table in the LISTED order: the run results come back in the order of $steps.
  $next = 0
  foreach ($e in $entries) {
    if ($e.PSObject.Properties["Skipped"]) { [void]$script:results.Add([pscustomobject]@{ Step = $e.Name; Result = "SKIPPED"; Seconds = 0; WaitSeconds = 0 }); continue }
    if ($e.PSObject.Properties["Failure"]) { [void]$script:results.Add([pscustomobject]@{ Step = $e.Name; Result = "FAIL"; Seconds = 0; WaitSeconds = $waitRow }); $waitRow = 0; continue }
    $r = $ran[$next]; $next++
    [void]$script:results.Add([pscustomobject]@{ Step = $r.Name; Result = $(if ($r.Outcome -eq "PASS") { "PASS" } else { "FAIL" }); Seconds = $r.Seconds; WaitSeconds = $waitRow })
    $waitRow = 0
    if ($r.Outcome -ne "PASS") { $script:failed = $true }
  }
  Remove-Item -LiteralPath $logRoot -Recurse -Force -ErrorAction SilentlyContinue
}

function Write-GateSummary {
  # The table and the wall time. The final word and the exit code stay at the script's end in
  # their old words (team-integrate.tests.ps1 reads them there).
  param([double]$WallSeconds)
  Write-Host ""
  Write-Host "=== Quality gate summary ===" -ForegroundColor Cyan
  $script:results | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
  $totalWait = 0
  foreach ($r in $script:results) { $totalWait += $r.WaitSeconds }
  Write-Host "Test queue wait, total: $totalWait s"
  $unmatched = Get-Variable -Name OnlyStepUnmatched -Scope Script -ValueOnly -ErrorAction SilentlyContinue
  foreach ($p in @($unmatched | Where-Object { $_ })) { Write-Host "OnlyStep: no step matched '$p'" -ForegroundColor Yellow }
  Write-Host "gate wall time: $([math]::Round($WallSeconds)) s"
}

function Find-Dotnet {
  # Never PATH: a spawned shell on this machine does not reliably have it.
  foreach ($cand in @("$env:LOCALAPPDATA\Microsoft\dotnet\dotnet.exe", "C:\Program Files\dotnet\dotnet.exe")) {
    if (Test-Path $cand) {
      $sdks = & $cand --list-sdks 2>$null
      if ($LASTEXITCODE -eq 0 -and $sdks) { return $cand }
    }
  }
  throw "dotnet SDK not found"
}

if ($StepList) {
  # Tests of the step machinery: the file's own Invoke-Step calls, then the summary and the
  # same final words as the script's end.
  . $StepList
  Write-GateSummary -WallSeconds $gateClock.Elapsed.TotalSeconds
  if ($script:failed) {
    Write-Host "QUALITY GATE: FAIL" -ForegroundColor Red
    exit 1
  }
  Write-Host "QUALITY GATE: PASS" -ForegroundColor Green
  exit 0
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
  # Forward slashes on purpose. This line shipped in d7f726a with \ separators,
  # and a string literal on the way in turned them into a carriage return and a
  # backspace: the names became "services<CR>ecovery-supervisor" and
  # "services<BS>rowser". The step then ran ruff at the repo root and printed
  # "All checks passed!" about two trees it had never entered. PowerShell takes /
  # everywhere, and no escape layer can mangle it.
  foreach ($svc in @("services/recovery-supervisor", "services/browser")) {
    $svcPath = Join-Path $repoRoot $svc
    # A path that is not there is a step that did not run. Never a silent pass again.
    if (-not (Test-Path $svcPath)) { throw "other-services lint: $svcPath does not exist" }
    Push-Location $svcPath
    try {
      & $uv run ruff check .
      Assert-ExitCode "ruff ($svc)"
    } finally { Pop-Location }
  }
}

# gate-unit-parallel (team/plans/gate-unit-parallel-adr.md): the unit suite ran 17,000 tests in
# one process for 30-50 minutes (2026-10-06), the longest step of the gate. It runs under
# pytest-xdist now, with a worker count from the machine; every test still runs and a failure
# is still a failure. Without xdist (or on a small or full machine) it runs serially as before.
# PAGENTOS_GATE_UNIT_WORKERS sets the count by hand (1 = serial).
function Get-GateUnitMemoryFloorGb {
  # The memory the machine keeps for everything else: team/cycle-settings.json
  # test_memory_floor_gb (1..64, the test queue's own floor), 8 when missing or malformed.
  param([string]$SettingsPath)
  try {
    $value = (Get-Content -LiteralPath $SettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json).test_memory_floor_gb
    if ($null -ne $value -and [string]$value -match '^\d+$' -and [int]$value -ge 1 -and [int]$value -le 64) { return [int]$value }
  } catch { }
  return 8
}

function Get-GateUnitWorkerCount {
  # 0 = serial. min(8, cores - 2), lowered to what the free memory above the floor holds.
  # 2 GB a worker: measured 2026-10-06 on 28 cores - one worker is ~1.07 GB once it has
  # collected the suite, and the whole tree peaked at 14.0 GB with 8 workers (1.75 a worker)
  # and 10.9 GB with 12. Twelve workers were not faster than eight on the shared machine
  # (1567 s against 1470 s), so eight stays the cap.
  param([int]$Cores, [int64]$FreeBytes, [bool]$XdistPresent, [string]$Override = "", [int]$FloorGb = 8)
  if (-not $XdistPresent) { return 0 }
  if ($Override -match '^\s*\d+\s*$') {
    $n = [int]$Override
    if ($n -lt 2) { return 0 }
    return $n
  }
  $max = 8; $floorGb = $FloorGb; $workerGb = 2
  $n = [Math]::Min($max, $Cores - 2)
  $byMemory = [int][Math]::Floor(($FreeBytes - [int64]$floorGb * 1GB) / ([int64]$workerGb * 1GB))
  $n = [Math]::Min($n, $byMemory)
  if ($n -lt 2) { return 0 }
  return [int]$n
}

Invoke-Step "API unit tests" -Kinds heavy {
  if (-not $uv) { throw "uv not found" }
  Push-Location $apiRoot
  try {
    & $uv run python -c "import xdist" 2>$null | Out-Null
    $xdist = ($LASTEXITCODE -eq 0)
    $freeBytes = [int64]0
    try { $freeBytes = [int64](Get-CimInstance -ClassName Win32_OperatingSystem).FreePhysicalMemory * 1KB } catch { $freeBytes = [int64]0 }
    $floorGb = Get-GateUnitMemoryFloorGb -SettingsPath (Join-Path $repoRoot "team\cycle-settings.json")
    $workers = Get-GateUnitWorkerCount -Cores ([Environment]::ProcessorCount) -FreeBytes $freeBytes -XdistPresent $xdist -Override ([string]$env:PAGENTOS_GATE_UNIT_WORKERS) -FloorGb $floorGb
    $machine = "{0} cores, {1:0.0} GB free, {2} GB floor" -f [Environment]::ProcessorCount, ($freeBytes / 1GB), $floorGb
    if ($workers -ge 2) {
      Write-Host "API unit tests: $workers xdist workers ($machine)"
      & $uv run pytest tests/unit -q -n $workers --dist load -m "not serial_tail"
      $parallelExit = $LASTEXITCODE
      # The tests that cannot run beside another (tests/conftest.py SERIAL_TAIL, each with its
      # reason), serially after the parallel part - also when it failed: their result is evidence.
      Write-Host "API unit tests: the serial tail (tests/conftest.py SERIAL_TAIL)"
      & $uv run pytest tests/unit -q -m serial_tail
      if ($parallelExit -ne 0) { $global:LASTEXITCODE = $parallelExit }
    } else {
      $why = if (-not $xdist) { "pytest-xdist is not installed" } else { "the machine has room for fewer than two xdist workers, or PAGENTOS_GATE_UNIT_WORKERS says so" }
      Write-Host "API unit tests: serial ($why; $machine)"
      & $uv run pytest tests/unit -q
    }
    Assert-ExitCode "pytest (unit)"
  } finally { Pop-Location }
}

# B11 (2026-09-13): the device suite runs in -Fast too. `desktop.notify` was appended to the
# agent's ambient group, the -Fast gate said clean, and `BrowserDispatchTests`' manifest pin
# — a test written precisely so that growth is visible rather than inherited silently — only
# spoke after the commit was already pushed. It costs under a minute against a twelve-minute
# gate, and it removes the same class of surprise d7f726a removed for the other services'
# ruff: the local gate must judge what CI judges.
# B15 (2026-09-13): and the run says WHY a test failed. `-v q` alone printed the `[FAIL]`
# header and swallowed the message and the stack, so a single device failure in a
# twelve-minute gate cost a whole re-run just to learn which assertion it was. The console
# logger at `minimal` prints failures in full and stays silent about the 1055 that passed:
# eight lines on a green run, measured.
$script:DotnetTestLogger = "console;verbosity=minimal"

Invoke-Step "Windows agent build + tests" -Kinds heavy {
  $dotnet = Find-Dotnet
  $env:DOTNET_ROOT = Split-Path -Parent $dotnet
  Push-Location (Join-Path $repoRoot "devices/windows-agent")
  try {
    if ($Fast) {
      # Release only, and one build: it is the configuration `qualify-staged-update.ps1`
      # judges, so it is the one worth a fast pass. The full gate below still does both.
      & $dotnet build PagentOS.WindowsAgent.sln -c Release --nologo -v q
      Assert-ExitCode "dotnet build -c Release"
      & $dotnet test PagentOS.WindowsAgent.sln -c Release --nologo --no-build -v q --logger $script:DotnetTestLogger
      Assert-ExitCode "dotnet test -c Release"
    } else {
      & $dotnet build PagentOS.WindowsAgent.sln --nologo -v q
      Assert-ExitCode "dotnet build"
      & $dotnet test PagentOS.WindowsAgent.sln --nologo --no-build -v q --logger $script:DotnetTestLogger
      Assert-ExitCode "dotnet test"
      # Release too, because a LATER step judges it. `qualify-staged-update.ps1` takes
      # bin\Release as the candidate (falling back to Debug), so without this the gate
      # tests one binary and qualifies another - and a stale Release tree is qualified as
      # though it were the tree. Found 2026-09-11 when a new identity field read empty.
      & $dotnet build PagentOS.WindowsAgent.sln -c Release --nologo -v q
      Assert-ExitCode "dotnet build -c Release"
    }
  } finally { Pop-Location }
}

# B13 (2026-09-13): and this runs in -Fast too, for the same reason and by the same lesson.
# The step above was moved into -Fast an hour earlier and CI STILL found a second guard the
# local gate did not run: `qualify-staged-update.ps1` pinned the advertised capability count
# at 40, so the same `desktop.notify` failed three of its checks after the manifest pin had
# already been fixed. Two guards, one addition, two separate CI round trips - because the
# local gate ran neither. It uses the Release tree the step above just built, and it touches
# nothing live: its own sandbox, its own journal, its own fake SCM.
Invoke-Step "Staged-update qualification (real candidate binary, sandbox engine)" {
  if (-not $powershell5) { throw "powershell.exe not found" }
  & $powershell5 -NoProfile -File (Join-Path $repoRoot "scripts/qualify-staged-update.ps1")
  Assert-ExitCode "staged-update qualification"
}

# ---------------------------------------------------------------- full checks

if (-not $Fast) {
  Invoke-Step "Dev stack up (docker compose)" -Kinds database {
    if (-not $powershell5) { throw "powershell.exe not found" }
    & $powershell5 -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot "scripts\dev-up.ps1")
    Assert-ExitCode "dev-up.ps1"
  }

  # gate-own-database (ADR-0254 open decision 5, ruled (b); team/plans/gate-faster-adr.md): the
  # two steps that touch PostgreSQL run against a database of the gate's own, made on the dev
  # server at the first step's start and dropped in the finally, so an inspector's integration
  # run on `pagentos` and this gate no longer reset one database under each other. The gate
  # never connects to `pagentos`. The variable is the one the application's Settings read
  # (tests/unit/test_gate_database_contract.py asks them); it is set for these steps'
  # children only. A gate that is killed leaves its database behind: the next one's sweep
  # drops gate databases older than 24 hours by the stamp in their names.
  $script:GateDatabaseVariable = "PAGENTOS_DATABASE_URL"
  $script:GateDatabase = Get-GateDatabaseName -RunId ("{0}_{1}" -f $PID, [guid]::NewGuid().ToString("N").Substring(0, 6)) -Now ([datetime]::UtcNow)
  $script:GateDatabaseUrl = $null
  $script:GateDatabaseCreated = $false
  try {
    Invoke-Step "Alembic upgrade head" -Kinds database {
      [void](Invoke-GateDatabaseSweep -Now ([datetime]::UtcNow))
      New-GateDatabase -Name $script:GateDatabase
      $script:GateDatabaseCreated = $true
      $script:GateDatabaseUrl = Get-GateDatabaseUrl -BaseUrl (Get-GateSettingsDatabaseUrl -Uv $uv -ApiRoot $apiRoot) -Name $script:GateDatabase
      Push-Location $apiRoot
      try {
        Invoke-WithGateDatabase -Variable $script:GateDatabaseVariable -Value $script:GateDatabaseUrl -Action {
          & $uv run alembic upgrade head
          Assert-ExitCode "alembic upgrade"
        }
      } finally { Pop-Location }
    }

    Invoke-Step "API integration tests" -Kinds database,heavy {
      if (-not $script:GateDatabaseUrl) { throw "no gate database: the step before this one did not make it" }
      Write-Host "database: $($script:GateDatabase) (the gate's own; pagentos is not touched)"
      Push-Location $apiRoot
      try {
        Invoke-WithGateDatabase -Variable $script:GateDatabaseVariable -Value $script:GateDatabaseUrl -Action {
          & $uv run pytest tests/integration -q -m integration
          Assert-ExitCode "pytest (integration)"
        }
      } finally { Pop-Location }
    }
  } finally {
    if ($script:GateDatabaseCreated) {
      Invoke-Step "Gate database dropped" {
        Remove-GateDatabase -Name $script:GateDatabase
      }
    }
  }

  Invoke-Step "Gate database tool (PS5.1 + the dev server)" -Kinds database {
    # scripts/lib/GateDatabase.ps1 against the dev stack's real PostgreSQL: a database the
    # migrations run on, the name rule, two at once, the sweep, no password. Not grouped: it
    # makes and drops databases on the server the integration run above just used.
    if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
    $script = Join-Path $repoRoot "scripts\tests\gate-database.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "gate-database tests"
  }

  # gate-parallel-suites (team/plans/gate-faster-adr.md): the PowerShell / bash suites from here
  # to Complete-GateGroup share no fixed port, temp path, database, git worktree or desktop -
  # the ADR gives the evidence per suite - and run side by side, each as its own process, at
  # most -GateMaxParallel at once. The two grouped suites that start the fake team API share
  # one lane: one at a time, listed first because together they are the group's longest path. What shares
  # something stays below the group, in the old order. -GateSerial runs it all one by one.
  Start-GateGroup

  Invoke-Step "Agent team integrate step (PS5.1 + git, fake gate, no model)" {
    # ADR-0260: scripts/team/integrate.ps1 gates a merged integration branch and puts exactly
    # the gated commit on main - against a sandbox repository, a fake gate, a fake lead and
    # the fake team API. The step is on main and NOT scheduled; this suite is what keeps it
    # honest until it is. About 25 minutes: the longest PowerShell step of the gate.
    if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
    $script = Join-Path $repoRoot "scripts\tests\team-integrate.tests.ps1"
    Invoke-GateSuite $script -Lane "fake-team-api"
    Assert-ExitCode "team-integrate tests"
  }

  Invoke-Step "Agent team roadmap feeder (PS5.1 + git, no model)" {
    # ADR-0214 addendum 8: the feeder that cuts the roadmap's next items into cards when the
    # worker seats would idle, and the judge of what the lead run wrote - against a fake.
    if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
    $script = Join-Path $repoRoot "scripts\tests\team-feed.tests.ps1"
    Invoke-GateSuite $script -Lane "fake-team-api"
    Assert-ExitCode "team-feed tests"
  }

  Invoke-Step "Cloud Core blue/green release (PS5.1 + Git Bash)" -Kinds heavy {
    # M18.4 (spec §6): the idle colour is brought up on the new sha, verified, switched to,
    # the old colour drained; rollback is the switch in reverse. Proven under a fake docker
    # that knows the two colours and the edge; the first real handoff is the next release.
    $script = Join-Path $repoRoot "scripts\tests\cloud-release-bluegreen.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "cloud release blue/green tests"
  }

  Invoke-Step "Script syntax (PowerShell 5.1)" {
    # A PowerShell 7-only construct is a parse error on the owner's 5.1 machine, so the script
    # dies on its first line. One such slip reached a credential-rotation script.
    $script = Join-Path $repoRoot "scripts\tests\script-syntax.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "script syntax tests"
  }

  Invoke-Step "Machine-readable child protocol" {
    # A rotation committed and lost its replacement credential because the child mixed a log
    # line into machine-readable stdout and the wrapper parsed leniently. These drive a real
    # child through every contamination shape and assert no error path quotes the secret.
    $script = Join-Path $repoRoot "scripts\tests\machine-readable.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "machine-readable protocol tests"
  }

  Invoke-Step "Installer invocation tests" {
    # The installer's native-tool invocation is a deterministic, offline check, and it earns
    # its place in the gate: a real install on the owner's machine failed at `sc create` with
    # ERROR_INVALID_COMMAND_LINE because PowerShell mangled an argument containing quotes.
    # These tests assert the exact argument shape and round-trip argv through a real child.
    $script = Join-Path $repoRoot "scripts\tests\installer-invocation.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "installer invocation tests"
  }

  Invoke-Step "Installer PS5.1 StrictMode tests" {
    # Windows PowerShell 5.1 cardinality: an elevated rerun died on `.Count` over a returned
    # empty collection, which unrolls to $null. These cover 0/1/many for every
    # collection-returning function and lint the pattern out of the installer scripts.
    $script = Join-Path $repoRoot "scripts\tests\installer-strictmode.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "installer strictmode tests"
  }

  Invoke-Step "Deployment transaction tests" {
    # A real deployment renamed the live service directory while the service ran from it and
    # left an asymmetric half-state. These drive the journaled engine through the incident
    # shape, every failure leg, and recovery from the exact partial state it left.
    $script = Join-Path $repoRoot "scripts\tests\installer-deploy.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "deployment transaction tests"
  }

  Invoke-Step "Installer ACL + recovery tests" {
    # Also deterministic and offline: builds real hardened trees in %TEMP%, reproduces the
    # empty-DACL state a previous install left on the owner's machine, and proves the
    # installer recovers from it without weakening anything.
    $script = Join-Path $repoRoot "scripts\tests\installer-acl.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "installer ACL tests"
  }

  Invoke-Step "Installer browser-worker provisioning tests (PS 5.1)" {
    # M13 (ADR-0050): the installer provisions the Browser Worker venv into the agent's
    # install tree, self-checks it in staging and after publish, and writes the companion's
    # worker configuration; these tests pin that transaction without touching an install.
    $script = Join-Path $repoRoot "scripts\tests\installer-browser.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "installer browser tests"
  }

  Invoke-Step "Owner Chrome enrollment record tests (PS 5.1)" {
    # 2026-09-29: the enrollment script printed "recorded:" and then "Access is denied" - an
    # icacls refusal piped to Out-Null, on a record an elevated run had created - and -Revoke
    # could not delete that record either. These reproduce the record's exact access in a
    # sandbox and pin the write, the read-back, the permissions and the revoke. The script
    # itself is parsed, never run: it closes and relaunches Chrome.
    $script = Join-Path $repoRoot "scripts\tests\owner-enrollment.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "owner enrollment record tests"
  }

  Invoke-Step "Installer evidence + engine wiring tests (PS 5.1)" {
    # 2026-09-03: the installer's inline swap failed under running processes and looked
    # successful; these pin the journaled engine as the only deploy path, the evidence
    # block, and the fail-loud M13 support assertion.
    $script = Join-Path $repoRoot "scripts\tests\installer-evidence.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "installer evidence tests"
  }

  Invoke-Step "Staged-update candidate + Cloud Core verification tests (PS 5.1)" {
    # 2026-09-08: the staged-update code (candidate manifest, Cloud Core heartbeat check)
    # had a test suite that NO GATE RAN. It passed against a device row it had invented,
    # while production returned a different shape, and a healthy 0.6.0 candidate was rolled
    # back. The suite is a gate now; the shape it uses is held by
    # services/api/tests/unit/test_device_identity_contract.py from the other side.
    $script = Join-Path $repoRoot "scripts\tests\agent-update.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "agent staged-update tests"
  }

  Invoke-Step "Agent audit reader (PS5.1)" {
    # The cloud driver reported "no command row found" while real commands succeeded:
    # its verifier read the timestamp from `at`, a field the writer never emitted (`ts`).
    # These pin the reader to the real schema and to identity-based correlation.
    $script = Join-Path $repoRoot "scripts\tests\agent-audit.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "agent audit tests"
  }

  Invoke-Step "Owner explain harness (PS5.1)" {
    # 2026-09-04: the M16 harness started the web shell without waiting and then required a
    # session newer than its own start time; these pin readiness gating and correlation.
    $script = Join-Path $repoRoot "scripts\tests\owner-explain.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "owner explain harness tests"
  }

  Invoke-Step "Cloud secret shipping (PS5.1)" {
    # The owner's provider-credential path: DPAPI store -> Tailscale SSH stdin -> /opt/pagentos/.env.
    # Proven with a real native fake ssh (5.1 native-argument quoting byte for byte, value
    # only ever on stdin) and pinned to tr-TR for the Turkish-I case-folding bug that
    # refused every secret name containing an I on the owner's machine.
    $script = Join-Path $repoRoot "scripts\tests\cloud-secret.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "cloud secret tests"
  }

  Invoke-Step "Cloud Core release transaction (PS5.1 + Git Bash)" {
    # ADR-0042: the host ran a copied tree from the first deployment, so a "restart" after
    # installing a secret changed nothing. The release (git archive -> app.next -> validate
    # -> swap -> build -> migrate -> recreate ONLY the api -> verify -> rollback on failure)
    # is proven here with a real native fake ssh/scp and a fake docker under Git Bash.
    $script = Join-Path $repoRoot "scripts\tests\cloud-release.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "cloud release tests"
  }

  Invoke-Step "Config swap transaction (PS5.1)" {
    # A real broker switch died inside [IO.File]::Replace: PowerShell binds $null to a
    # [string] parameter as an EMPTY string, which .NET refuses as a path. These reproduce
    # it and prove the transactional replace: same-volume staging + real backup, stale
    # staging files, existing backups, validation before going live, rollback, idempotence.
    $script = Join-Path $repoRoot "scripts\tests\config-swap.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "config swap tests"
  }

  Invoke-Step "Provisioning + parameter collisions (PS5.1)" {
    # A real provisioning run applied four billable resources and then died assigning the
    # result over its own [switch]$Apply parameter - PowerShell variable names are
    # case-insensitive - so it looked like it had stopped before applying. These drive
    # provision.ps1 end to end through a fake tofu and lint every script for the class.
    $script = Join-Path $repoRoot "scripts\tests\provision.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "provisioning tests"
  }

  Invoke-Step "Identity restoration (PS5.1 + real key)" {
    # A real finalize run died calling ECDsa.ImportFromPem from Windows PowerShell 5.1,
    # whose .NET Framework does not have it. These run AFTER the agent build: the real
    # service exe performs a real loopback enrollment, and the `identity` verb must return
    # the enrolled public key byte-for-byte - the property broker-registration restore
    # depends on. Also proves the verb is load-only: asking never mints a key.
    $script = Join-Path $repoRoot "scripts\tests\identity-restore.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "identity restoration tests"
  }

  Invoke-Step "Native signing trust step (PS5.1)" {
    # B33 req 473: the owner's one elevated step imports ONLY the companion's self-signed
    # public certificate into LocalMachine\TrustedPeople. Every check and store operation
    # runs here against throwaway CURRENT-USER stores; no LocalMachine store is written.
    $script = Join-Path $repoRoot "scripts\tests\native-signing-trust.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "native signing trust tests"
  }

  Invoke-Step "Cloud Core maintenance window script (PS5.1 + bash, fakes)" {
    # ADR-0223: preflight / run / verify of scripts/cloud/maintenance-reboot.sh against a
    # fake docker, apt, systemctl and curl. Nothing here touches a host.
    if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
    $script = Join-Path $repoRoot "scripts\tests\maintenance-reboot.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "maintenance-reboot tests"
  }

  Invoke-Step "Cloud Core host snapshot (PS5.1 + bash, fakes; the real fixture)" {
    # The read-only snapshot script, its allow-list of commands, the collector and the schema,
    # against fakes; the fixture the fake hosts are built from was collected from the real host.
    if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
    $script = Join-Path $repoRoot "scripts\tests\host-snapshot.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "host-snapshot tests"
  }

  Invoke-Step "Web shell on the tailnet: HTTPS script (PS5.1 + bash, fake tailscale)" {
    # The web shell runs on the Cloud Core (aux `web` service, loopback only); the phone reaches
    # it over `tailscale serve` HTTPS. The script that sets that up, against a fake tailscale:
    # the loopback target, idempotence, --off, and that `funnel` is never called. No tailnet.
    if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
    $script = Join-Path $repoRoot "scripts\tests\web-tailnet-https.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "web-tailnet-https tests"
  }

  Invoke-Step "Agent team board client (PS5.1, fake board, no model)" {
    # The team's board (the owner's idea, 2026-10-03): scripts/team/board.ps1 posts and reads
    # notes against a fake board on 127.0.0.1; an unreachable board is a warning and exit 0.
    # Grouped: its fake board takes a port the system gives (port 0) and its folder is a GUID.
    if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
    $script = Join-Path $repoRoot "scripts\tests\team-board.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "team-board tests"
  }

  Invoke-Step "API unit step in parallel (PS5.1, fake uv + pytest -n 2)" {
    # gate-unit-parallel: the worker count, the -n call, the serial fallback without xdist and
    # a failure under -n, against the gate itself with a fake uv; then the real pytest -n 2 on
    # the files whose ids once differed between workers. Grouped: its folder is a GUID, it
    # opens no port and pytest runs without its cache.
    if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
    $script = Join-Path $repoRoot "scripts\tests\gate-unit-parallel.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "gate-unit-parallel tests"
  }

  Complete-GateGroup

  # Below the group, one by one as before: what shares something with a grouped suite or with
  # the desktop.
  Invoke-Step "Agent team cycle (PS5.1 + git, no model)" -Kinds heavy {
    # docs/TEAM_PROTOCOL.md: the queue, the lock, the role runs and the report, with a
    # fake in place of the model and a git repository made for the test.
    # Not grouped: its pool cases time seats against each other and were red beside the group
    # (2026-10-03, two of 214) and green alone - it runs with nothing beside it.
    if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
    $script = Join-Path $repoRoot "scripts\tests\team-cycle.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "team-cycle tests"
  }

  Invoke-Step "Agent team tick not held by orphans (PS5.1, no model)" {
    # 2026-10-03: a `tail -f` an agent run left behind held the scheduled tick (Start-Process
    # -Wait waits for every descendant) and no cycle ran for two hours. The tick waits for its
    # script's own process and stops what is left in the job it owns - fakes in place of both.
    # Not grouped: its case (5) holds a tick to a fixed 60 s guard; beside the group it passed
    # 60 s and was red, alone the whole suite took 25 s (2026-10-05, this branch's runs).
    if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
    $script = Join-Path $repoRoot "scripts\tests\team-tick.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "team-tick tests"
  }

  Invoke-Step "Gate suite group (PS5.1, fake steps)" {
    # scripts/lib/GateSteps.ps1, the group this gate runs its suites in. Not grouped: its cases
    # time fake steps against each other (three at once, a lane, a deadline).
    if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
    $script = Join-Path $repoRoot "scripts\tests\gate-steps.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "gate-steps tests"
  }

  Invoke-Step "Agent team area widening rules (PS5.1, no model)" {
    # A fix outside a card's area: the request line of a report, the widen / wait / refuse
    # judgement and the protected paths (scripts/lib/TeamArea.ps1) - functions only.
    # Not grouped: the suite reads this step's own text and wants the 5.1 call written here
    # (team-area.tests.ps1, "gate: quality-gate.ps1 runs this suite as its own step"); 2 s.
    if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
    $script = Join-Path $repoRoot "scripts\tests\team-area.tests.ps1"
    & $powershell5 -NoProfile -ExecutionPolicy Bypass -File $script
    Assert-ExitCode "team-area tests"
  }

  Invoke-Step "Agent team test queue (PS5.1, real wrapper processes, temp stores)" {
    # The owner's idea of 2026-10-02 (ONAY / BEKLE before a heavy run): the queue's rules with
    # real wrapper processes and a store per case, and this gate's own use of it (-StepList).
    # Not grouped: its cases wait on real processes with deadlines and start this gate itself.
    if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
    $script = Join-Path $repoRoot "scripts\tests\team-test-slots.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "team-test-slots tests"
  }

  Invoke-Step "Agent team run temp folder kept for Git Bash /tmp (PS5.1, fake mount)" {
    # A run's temp folder is emptied, not deleted: Git Bash may hold it as the machine's /tmp
    # (2026-10-06 01:50); the sweep removes only empty, old folders that are not the /tmp mount.
    if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
    $script = Join-Path $repoRoot "scripts\tests\team-run-temp.tests.ps1"
    & $powershell5 -NoProfile -ExecutionPolicy Bypass -File $script
    Assert-ExitCode "team-run-temp tests"
  }

  Invoke-Step "Agent team automatic release, resume after a limit, staging scripts (PS5.1, fakes)" {
    # ADR-0287 (cycle-auto-release), ADR-0290 (limit-resume-session), ADR-0294 (staging-stack):
    # fakes only - no release, no model and no container is started here.
    # Not grouped: three suites in one step (a recorded step runs one script), and staging's fake
    # health probes a free port and releases it before listening (the fake-team-api lane's race).
    if (-not $powershell5) { throw "Windows PowerShell 5.1 not found" }
    foreach ($name in @("team-release", "team-resume", "staging", "testteam", "team-gate-second-look", "team-liveness")) {
      & $powershell5 -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot "scripts\tests\$name.tests.ps1")
      Assert-ExitCode "$name tests"
    }
  }

  Invoke-Step "UTF-8 JSON decoding (PS5.1)" {
    # A real qualification record showed Turkish letters as mojibake: 5.1 decoded a
    # charset-less JSON body as Latin-1 while the database held correct UTF-8.
    # Not grouped: its listener is on the FIXED port 127.0.0.1:18099.
    $script = Join-Path $repoRoot "scripts\tests\utf8-json.tests.ps1"
    Invoke-GateSuite $script
    Assert-ExitCode "utf8 json tests"
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

  Invoke-Step "Browser agent lint + tests" -Kinds heavy {
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

  if ($E2E) {
    # Opens Notepad on the owner's desktop: never beside another window-opening step.
    Invoke-Step "M1 device E2E (Notepad)" -Kinds desktop {
      & $powershell5 -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot "scripts\e2e-m1-device.ps1") -SkipBuild
      Assert-ExitCode "e2e-m1-device.ps1"
    }
  }

  Invoke-Step "Web shell build" -Kinds heavy {
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

  Invoke-Step "Web shell lint, unit tests and types (oxlint, vitest, tsc)" -Kinds heavy {
    # CI's web job, here: GitHub Actions is off (2026-09-19), so this gate is the only place
    # the web suite is ever run before a release. After the build, as in ci.yml: tsconfig
    # includes the types `next build` generates.
    $pnpm = Resolve-Tool "pnpm" @("%APPDATA%\npm\pnpm.cmd", "%LOCALAPPDATA%\pnpm\pnpm.exe")
    if (-not $pnpm) { throw "pnpm not found" }
    Push-Location $repoRoot
    try {
      & $pnpm --dir apps\web lint
      Assert-ExitCode "oxlint (apps/web)"
      & $pnpm --dir apps\web test
      Assert-ExitCode "vitest (apps/web)"
      & $pnpm --dir apps\web typecheck
      Assert-ExitCode "tsc --noEmit (apps/web)"
    } finally { Pop-Location }
  }
}

# -------------------------------------------------------------------- summary

Write-GateSummary -WallSeconds $gateClock.Elapsed.TotalSeconds

if ($failed) {
  Write-Host "QUALITY GATE: FAIL" -ForegroundColor Red
  exit 1
}
Write-Host "QUALITY GATE: PASS" -ForegroundColor Green
exit 0
