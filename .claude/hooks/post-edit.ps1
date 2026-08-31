$ErrorActionPreference = "SilentlyContinue"
$gate = Join-Path $env:CLAUDE_PROJECT_DIR "scripts\quality-gate.ps1"
if (Test-Path $gate) {
    & $gate -Fast
}
exit 0
