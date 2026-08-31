param([switch]$Fast)
$ErrorActionPreference = "Stop"

# This bootstrap gate is intentionally lightweight before M0 implementation.
# Claude should expand it to invoke the real monorepo test commands as services are created.

$required = @(
  "PROJECT_CONSTITUTION.md",
  "docs/MASTER_SPEC.md",
  "docs/ARCHITECTURE.md",
  "docs/ACCEPTANCE_TESTS.md",
  "state/BUILD_STATE.json"
)

foreach ($path in $required) {
  if (-not (Test-Path (Join-Path $PSScriptRoot "..\$path"))) {
    Write-Error "Missing required file: $path"
    exit 1
  }
}

if (Test-Path (Join-Path $PSScriptRoot "..\.env")) {
  Write-Error ".env exists at repository root. Ensure it is gitignored and secrets are not tracked."
}

Write-Host "Bootstrap quality gate passed. Expand this script during M0."
exit 0
