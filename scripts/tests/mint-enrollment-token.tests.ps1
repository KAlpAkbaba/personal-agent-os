<#
.SYNOPSIS
    Windows PowerShell 5.1 StrictMode tests for scripts/cloud/mint-enrollment-token.sh
    (ADR-0203), under Git Bash with a fake docker and a fake Cloud Core on loopback.

.DESCRIPTION
    The script is the only way a SECOND device gets an enrolment token (the route is
    loopback-only), and it handles the owner credential, so what it must never do matters
    as much as what it does. Proven here, with no real docker, host or credential:

      * the token is minted inside the ACTIVE colour's container; the single-container
        name is used only when no colour is recorded;
      * the conversation is session -> token -> revoke, in that order, and the session is
        revoked even when the token route refuses;
      * the credential crosses on stdin: it is in no process argument and in no output;
      * a refused credential (66), a refused token route (67), no credential (64) and no
        running container (65) each end with their own exit code and NO token printed.

    The fake docker runs the script's python with the local interpreter, so the python
    body is executed for real against the fake api - not merely passed along.

    Run: powershell -NoProfile -File scripts\tests\mint-enrollment-token.tests.ps1
#>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$hostScript = Join-Path $repoRoot "scripts\cloud\mint-enrollment-token.sh"
$bash = Join-Path $env:ProgramFiles "Git\bin\bash.exe"
$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-mint-token-tests-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passes++; Write-Host "  PASS  $Message" }
    else { $script:Failures++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
}

$posix = { param($p) $p = ($p -replace '\\', '/'); if ($p -match '^([A-Za-z]):(.*)$') { '/' + $Matches[1].ToLower() + $Matches[2] } else { $p } }

if (-not (Test-Path -LiteralPath $bash)) { throw "Git Bash not found at $bash" }
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) { throw "python is needed to run the script's own python body; none on PATH" }
$python = $pythonCommand.Source

$fakeBin = Join-Path $script:Sandbox "bin"
$state = Join-Path $script:Sandbox "state"
$edge = Join-Path $script:Sandbox "edge"
New-Item -ItemType Directory -Force -Path $fakeBin, $state, $edge | Out-Null

# A syntactically plausible credential that is not, and never was, a real one.
$script:Credential = "pagentos_ok_" + ("T" * 43)
$script:EnrolToken = "enrol-" + ("E" * 32)

# ---------------------------------------------------------------- the fake docker
#
# `docker ps` lists what state/running names; `docker exec -i <container> python3 -c CODE
# ARGS` runs CODE with the local interpreter, stdin passed through. Every call's ARGUMENTS
# are logged - which is where a credential passed the wrong way would show up.
$docker = @(
    '#!/usr/bin/env bash',
    'printf "docker %s\n" "$*" >> "$FAKE_STATE/calls.log"',
    'if [ "$1" = "ps" ]; then cat "$FAKE_STATE/running" 2>/dev/null; exit 0; fi',
    'if [ "$1" = "exec" ]; then',
    '  shift; [ "$1" = "-i" ] && shift',
    '  printf "%s\n" "$1" >> "$FAKE_STATE/exec-containers.log"',
    '  shift; shift   # container, python3',
    '  exec "$FAKE_PYTHON" "$@"',
    'fi',
    'exit 0'
) -join "`n"
[IO.File]::WriteAllText((Join-Path $fakeBin "docker"), $docker + "`n", (New-Object Text.UTF8Encoding($false)))

# ---------------------------------------------------------------- the fake Cloud Core
$server = @'
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

state, credential, enrol = sys.argv[1], sys.argv[2], sys.argv[3]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        auth = self.headers.get("Authorization") or ""
        with open(os.path.join(state, "requests.log"), "a") as log:
            log.write("%s auth=%s\n" % (self.path, "yes" if auth == "Bearer sess-tok" else "no"))
        if self.path == "/v1/identity/sessions":
            sent = json.loads(body)
            if sent.get("owner_credential") != credential:
                return self.reply(401, {"detail": "refused"})
            with open(os.path.join(state, "session-request.json"), "w") as out:
                sent["owner_credential"] = "<redacted>"
                json.dump(sent, out)
            return self.reply(201, {"token": "sess-tok", "session_id": "sid-1"})
        if auth != "Bearer sess-tok":
            return self.reply(401, {"detail": "no session"})
        if self.path == "/v1/devices/enrollment-tokens":
            if os.path.exists(os.path.join(state, "refuse-token")):
                return self.reply(403, {"detail": "loopback only in dev"})
            return self.reply(201, {"token": enrol, "expires_at": "2026-09-28T12:15:00Z"})
        if self.path == "/v1/identity/sessions/sid-1/revoke":
            return self.reply(200, {"revoked": True})
        return self.reply(404, {"detail": "Not Found"})


httpd = HTTPServer(("127.0.0.1", 0), Handler)
with open(os.path.join(state, "port.txt"), "w") as out:
    out.write(str(httpd.server_address[1]))
httpd.serve_forever()
'@
$serverPath = Join-Path $script:Sandbox "fake_core.py"
[IO.File]::WriteAllText($serverPath, $server, (New-Object Text.UTF8Encoding($false)))

$serverProcess = Start-Process -FilePath $python -ArgumentList @("`"$serverPath`"", "`"$state`"", $script:Credential, $script:EnrolToken) `
    -PassThru -WindowStyle Hidden
$portFile = Join-Path $state "port.txt"
$deadline = (Get-Date).AddSeconds(20)
while (-not (Test-Path -LiteralPath $portFile) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200 }
if (-not (Test-Path -LiteralPath $portFile)) { throw "the fake Cloud Core did not start" }
$port = (Get-Content -LiteralPath $portFile -Raw).Trim()

function Reset-State {
    param([string[]]$Running = @(), [string]$Active = "")
    foreach ($name in @("calls.log", "exec-containers.log", "requests.log", "session-request.json", "refuse-token", "running")) {
        Remove-Item -LiteralPath (Join-Path $state $name) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath (Join-Path $edge "active.txt") -Force -ErrorAction SilentlyContinue
    [IO.File]::WriteAllText((Join-Path $state "running"), (($Running -join "`n") + "`n"))
    if ($Active) { [IO.File]::WriteAllText((Join-Path $edge "active.txt"), "$Active`n") }
}

function Read-Log {
    param([string]$Name)
    $path = Join-Path $state $Name
    if (Test-Path -LiteralPath $path) { return [IO.File]::ReadAllText($path) }
    return ""
}

function Invoke-Mint {
    param([string]$StandardInput = "", [string[]]$Arguments = @())
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $bash
    $argumentText = ($Arguments | ForEach-Object { "'$_'" }) -join " "
    # The credential is fed from a FILE the shell redirects, never through .NET's stdin
    # writer: under a UTF-8 console that writer emits a byte-order mark, and the
    # "credential" the script read began with it (seen on the first run of this suite).
    $stdinFile = Join-Path $script:Sandbox "stdin.txt"
    $stdinText = if ($StandardInput) { $StandardInput + "`n" } else { "" }
    [IO.File]::WriteAllText($stdinFile, $stdinText, (New-Object Text.UTF8Encoding($false)))
    $command = "export PATH='$(& $posix $fakeBin)':`$PATH; bash '$(& $posix $hostScript)' $argumentText < '$(& $posix $stdinFile)'"
    $info.Arguments = "-c `"$command`""
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.CreateNoWindow = $true
    $info.EnvironmentVariables["FAKE_STATE"] = (& $posix $state)
    $info.EnvironmentVariables["FAKE_PYTHON"] = (& $posix $python)
    $info.EnvironmentVariables["PAGENTOS_EDGE_DIR"] = (& $posix $edge)
    $info.EnvironmentVariables["PAGENTOS_API_PORT"] = $port
    $process = [System.Diagnostics.Process]::Start($info)
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    if (-not $process.WaitForExit(60000)) { try { $process.Kill() } catch { }; throw "the script did not finish within 60 s" }
    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        StdOut   = $stdoutTask.Result
        StdErr   = $stderrTask.Result
    }
}

try {
    Write-Host ""
    Write-Host "mint-enrollment-token.sh (ADR-0203)"

    # ------------------------------------------------ 1. the ordinary mint, active colour
    Reset-State -Running @("pagentos-prod-api-green", "pagentos-prod-edge") -Active "green"
    $run = Invoke-Mint -StandardInput $script:Credential -Arguments @("ofis-enrolment")
    Assert-True ($run.ExitCode -eq 0) "a mint with the right credential exits 0 (got $($run.ExitCode); stderr: $($run.StdErr.Trim()))"
    Assert-True ($run.StdOut -match [regex]::Escape($script:EnrolToken)) "the token is printed on stdout"
    Assert-True ($run.StdOut -match "expires_at 2026-09-28T12:15:00Z") "the expiry is printed beside it"
    Assert-True ((Read-Log "exec-containers.log").Trim() -eq "pagentos-prod-api-green") "it runs inside the ACTIVE colour's container"
    $requests = @((Read-Log "requests.log") -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    Assert-True ($requests.Count -eq 3 -and $requests[0] -like "/v1/identity/sessions auth=no" `
        -and $requests[1] -like "/v1/devices/enrollment-tokens auth=yes" `
        -and $requests[2] -like "/v1/identity/sessions/sid-1/revoke auth=yes") "the conversation is session -> token -> revoke, in that order"
    $sessionRequest = (Read-Log "session-request.json") | ConvertFrom-Json
    Assert-True ($sessionRequest.label -eq "ofis-enrolment" -and $sessionRequest.client_kind -eq "cli" -and $sessionRequest.ttl_s -eq 300) "the session is labelled, a cli session, and lives 300 s at most"
    Assert-True (-not ((Read-Log "calls.log") -match [regex]::Escape($script:Credential))) "the credential is in NO process argument"
    Assert-True (-not (($run.StdOut + $run.StdErr) -match [regex]::Escape($script:Credential))) "the credential is in NO output"
    Assert-True (-not (($run.StdOut + $run.StdErr) -match "sess-tok")) "the session token is in NO output"

    # ------------------------------------------------ 2. a refused credential
    Reset-State -Running @("pagentos-prod-api-blue") -Active "blue"
    $run = Invoke-Mint -StandardInput "pagentos_ok_wrong"
    Assert-True ($run.ExitCode -eq 66) "a refused credential exits 66 (got $($run.ExitCode))"
    Assert-True (-not $run.StdOut.Trim()) "and prints nothing on stdout"
    Assert-True (-not ((Read-Log "requests.log") -match "enrollment-tokens")) "and never asks for a token"

    # ------------------------------------------------ 3. the token route refuses
    Reset-State -Running @("pagentos-prod-api-blue") -Active "blue"
    [IO.File]::WriteAllText((Join-Path $state "refuse-token"), "1")
    $run = Invoke-Mint -StandardInput $script:Credential
    Assert-True ($run.ExitCode -eq 67) "a refused token route exits 67 (got $($run.ExitCode))"
    Assert-True (-not ($run.StdOut -match "token")) "and prints no token"
    Assert-True ((Read-Log "requests.log") -match "/v1/identity/sessions/sid-1/revoke auth=yes") "and the session is STILL revoked"

    # ------------------------------------------------ 4. no credential
    Reset-State -Running @("pagentos-prod-api-blue") -Active "blue"
    $run = Invoke-Mint -StandardInput ""
    Assert-True ($run.ExitCode -eq 64) "no credential exits 64 (got $($run.ExitCode))"
    Assert-True (-not ((Read-Log "calls.log") -match "docker exec")) "and nothing is executed in any container"

    # ------------------------------------------------ 5. no api container
    Reset-State -Running @("pagentos-prod-postgres") -Active "blue"
    $run = Invoke-Mint -StandardInput $script:Credential
    Assert-True ($run.ExitCode -eq 65) "no running api container exits 65 (got $($run.ExitCode))"
    Assert-True (-not (Read-Log "requests.log")) "and the api is never called"

    # ------------------------------------------------ 6. the single-container topology
    Reset-State -Running @("pagentos-prod-api")
    $run = Invoke-Mint -StandardInput $script:Credential
    Assert-True ($run.ExitCode -eq 0 -and (Read-Log "exec-containers.log").Trim() -eq "pagentos-prod-api") "with no colour recorded it uses pagentos-prod-api"

    # ------------------------------------------------ 7. a recorded colour that is not up
    Reset-State -Running @("pagentos-prod-api-blue") -Active "green"
    $run = Invoke-Mint -StandardInput $script:Credential
    Assert-True ($run.ExitCode -eq 65) "a recorded colour that is not running is NOT replaced by the other colour (65; got $($run.ExitCode))"
}
finally {
    try { if (-not $serverProcess.HasExited) { $serverProcess.Kill() } } catch { }
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "$($script:Passes) passed, $($script:Failures) failed"
if ($script:Failures -gt 0) { exit 1 }
