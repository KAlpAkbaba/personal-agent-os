$ErrorActionPreference = "Continue"
$gate = Join-Path $env:CLAUDE_PROJECT_DIR "scripts\quality-gate.ps1"
if (-not (Test-Path $gate)) {
    exit 0
}
& $gate -Fast
if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine("Quality gate failed. Fix the failures before completing the task.")
    exit 2
}
exit 0
