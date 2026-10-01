<#
.SYNOPSIS
    Windows PowerShell 5.1 StrictMode tests for the read-only snapshot of the Cloud Core host:
    scripts/cloud/host-snapshot.sh under Git Bash with a fake docker/psql/systemctl/flock/
    uname/cat/stat/sleep on PATH and a sandbox in place of the host's files, and
    scripts/cloud/collect-host-snapshot.ps1 with a fake ssh.
.DESCRIPTION
    The snapshot is what the fake hosts of the other suites are built from, so the one thing
    it must never do is change the host it describes. Proven here, with no real host:
      * it prints ONE JSON document on stdout (status lines on stderr), the collector's
        schema holds for it, and its values are the sandbox's;
      * EVERY command it runs is on a read-only allow-list. The record is bash's own xtrace
        (every command word, builtins and functions included) plus the fakes' call log (the
        exact arguments): docker ps with names and states only, docker exec <postgres> psql
        -Atc with ONE statement that starts with SELECT and reads one information_schema
        relation, cat/stat of the named marker files, uname, systemctl list-timers/is-active,
        flock -n <the operation lock> true, sleep. Anything else fails the suite - 'docker
        inspect' too: the script does not need it and it can print a container's environment;
      * the recorder cannot be switched off: 'set' is allowed only as 'set -eu -o pipefail',
        a variable that is not the script's own (PS4, BASH_XTRACEFD, PATH) may be neither
        assigned nor named in the source, and the trace must reach the script's last command;
      * the allow-list itself refuses what it must: an UPDATE, a SELECT from an application
        table, a join onto one, docker exec of anything but that psql, a path under the base
        that is not a named marker;
      * xtrace does not show redirections, so the script may not hold one: no file is read
        with '<' or written with '>' (only '<<<', '>&2' and '2>/dev/null');
      * the sandbox is byte-for-byte the same after a run, a missing lock file is not created
        (flock would create it), and neither a decoy secret file nor an environment value
        reaches the output;
      * a failing docker gives a non-zero exit and NO document, not half of one;
      * the collector sends the script on stdin to 'bash -s' in BatchMode, never merges stderr,
        writes the fixture with collected_at only when the document is valid, and with an
        unreachable ssh writes nothing and exits 4.
    Run: powershell -NoProfile -File scripts\tests\host-snapshot.tests.ps1
#>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$hostScript = Join-Path $repoRoot "scripts\cloud\host-snapshot.sh"
$collector = Join-Path $repoRoot "scripts\cloud\collect-host-snapshot.ps1"
$fixture = Join-Path $repoRoot "scripts\tests\fixtures\host-snapshot.json"
$bash = Join-Path $env:ProgramFiles "Git\bin\bash.exe"
$powershellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-snapshot-tests-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passes++; Write-Host "  PASS  $Message" }
    else { $script:Failures++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
}

$u = { param($p) ($p -replace '\\', '/') }
$posix = { param($p) $p = ($p -replace '\\', '/'); if ($p -match '^([A-Za-z]):(.*)$') { '/' + $Matches[1].ToLower() + $Matches[2] } else { $p } }
$hostRoot = Join-Path $script:Sandbox "host"
$base = Join-Path $hostRoot "opt\pagentos"
$edge = Join-Path $hostRoot "edge"
$recovery = Join-Path $hostRoot "recovery"
$run = Join-Path $hostRoot "run"
$state = Join-Path $script:Sandbox "state"
$fakeBin = Join-Path $script:Sandbox "bin"
New-Item -ItemType Directory -Force -Path $fakeBin, $state | Out-Null
$sha = "3333333333333333333333333333333333333333"
$lkg = "1111111111111111111111111111111111111111"
$lockFile = & $u (Join-Path $base ".bluegreen-operation.lock")
# The marker files the task names; nothing else under the sandbox may be touched.
$namedFiles = @(
    (& $u (Join-Path $base "RELEASE")), (& $u (Join-Path $base "LAST_KNOWN_GOOD")), $lockFile,
    (& $u (Join-Path $edge "active.txt")), (& $u (Join-Path $recovery "APPROVED_SHA")), (& $u (Join-Path $run "reboot-required"))
)
$namedDirs = @((& $u $base), (& $u $edge), (& $u $recovery))
$sep = [string][char]0x1f
$traceMarker = "@@HS@@"

# ---- fakes: each logs its exact arguments (0x1f-separated) to $FAKE_STATE/calls.log ------
$logLine = '{ printf "%s" "$(basename "$0")"; for a in "$@"; do printf "\x1f%s" "$a"; done; printf "\n"; } >> "$FAKE_STATE/calls.log"'
$fakes = @{
    docker = @(
        '#!/usr/bin/env bash', $logLine,
        'if [ -n "${FAKE_DOCKER_FAIL:-}" ]; then echo "Cannot connect to the Docker daemon" >&2; exit 1; fi',
        'case "$1" in',
        '  ps) printf "%s\n" "$(<"$FAKE_STATE/docker-ps.txt")";;',
        '  exec) printf "%s\n" "$(<"$FAKE_STATE/columns.txt")";;',
        'esac',
        'exit 0')
    psql = @('#!/usr/bin/env bash', $logLine, 'exit 0')
    systemctl = @(
        '#!/usr/bin/env bash', $logLine,
        'case "$1" in',
        '  list-timers)',
        '    echo "Thu 2026-10-01 19:00:00 UTC 5h left    Wed 2026-09-30 19:00:02 UTC 18h ago pagentos-maintenance-window.timer   pagentos-maintenance-window.service"',
        '    echo "Thu 2026-10-01 13:01:00 UTC 20s left   Thu 2026-10-01 13:00:00 UTC 40s ago pagentos-bluegreen-reconcile.timer  pagentos-bluegreen-reconcile.service"',
        '    echo "-                           -          -                           -       pagentos-restore-drill.timer        pagentos-restore-drill.service";;',
        '  is-active) if [ "$2" = "pagentos-restore-drill.timer" ]; then echo inactive; exit 3; fi; echo active;;',
        'esac',
        'exit 0')
    flock = @(
        '#!/usr/bin/env bash', $logLine,
        'n=0; if [ -f "$FAKE_STATE/flock.n" ]; then n=$(<"$FAKE_STATE/flock.n"); fi',
        'n=$((n + 1)); printf "%s" "$n" > "$FAKE_STATE/flock.n"',
        'for held in ${FAKE_FLOCK_HELD:-}; do if [ "$held" = "$n" ]; then exit 1; fi; done',
        'exit 0')
    uname = @('#!/usr/bin/env bash', $logLine, 'echo "6.8.0-138-generic"')
    cat = @('#!/usr/bin/env bash', $logLine, 'exec /usr/bin/cat "$@"')
    stat = @('#!/usr/bin/env bash', $logLine, 'exec /usr/bin/stat "$@"')
    sleep = @('#!/usr/bin/env bash', $logLine, 'exit 0')
    "fake-ssh.sh" = @(
        '#!/usr/bin/env bash',
        'printf "%s\n" "$*" >> "$FAKE_STATE/ssh-args.log"',
        'if [ -n "${FAKE_SSH_FAIL:-}" ]; then echo "ssh: connect to host 100.64.0.1 port 22: Connection timed out" >&2; exit 255; fi',
        '/usr/bin/cat > "$FAKE_STATE/ssh-stdin.sh"',
        'echo "Warning: Permanently added the host (noise on stderr, never part of the document)" >&2',
        'if [ -n "${FAKE_SSH_GARBAGE:-}" ]; then echo "{ \"schema_version\": 1 }"; exit 0; fi',
        'PATH="$FAKE_BIN:$PATH" bash -s < "$FAKE_STATE/ssh-stdin.sh"')
}
foreach ($k in $fakes.Keys) { [IO.File]::WriteAllText((Join-Path $fakeBin $k), (($fakes[$k] -join "`n") + "`n")) }
$fakeSsh = Join-Path $fakeBin "fake-ssh.cmd"
[IO.File]::WriteAllText($fakeSsh, "@echo off`r`n`"$bash`" `"$(& $u (Join-Path $fakeBin 'fake-ssh.sh'))`" %*`r`n")

function Reset-Host {
    param([string]$Colour = "green", [switch]$NoLock, [switch]$Bare)
    Remove-Item -LiteralPath $hostRoot -Recurse -Force -ErrorAction SilentlyContinue
    foreach ($d in $base, $edge, $recovery, $run) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
    # A decoy the snapshot must never read: the env file with the host's secrets.
    [IO.File]::WriteAllText((Join-Path $base ".env"), "PAGENTOS_OWNER_TOKEN=hunter2-file`n")
    if (-not $NoLock) { [IO.File]::WriteAllText((Join-Path $base ".bluegreen-operation.lock"), "") }
    if ($Bare) {
        [IO.File]::WriteAllText((Join-Path $base "LAST_KNOWN_GOOD"), "not a sha at all`n")
        [IO.File]::WriteAllText((Join-Path $edge "active.txt"), "BLUE`n")
    }
    else {
        [IO.File]::WriteAllText((Join-Path $base "RELEASE"), "$sha`n")
        [IO.File]::WriteAllText((Join-Path $base "LAST_KNOWN_GOOD"), "$lkg`n")
        [IO.File]::WriteAllText((Join-Path $recovery "APPROVED_SHA"), "$sha`n")
        [IO.File]::WriteAllText((Join-Path $edge "active.txt"), "$Colour`n")
        [IO.File]::WriteAllText((Join-Path $run "reboot-required"), "*** System restart required ***`n")
    }
    $other = if ($Colour -eq "green") { "blue" } else { "green" }
    $ps = @("postgres", "redis", "minio", "temporal", "edge", "godseye", "cloud-browser", "api-$Colour") | ForEach-Object { "pagentos-prod-$_|running" }
    [IO.File]::WriteAllText((Join-Path $state "docker-ps.txt"), ((@($ps) + "pagentos-prod-api-$other|exited") -join "`n") + "`n")
    [IO.File]::WriteAllText((Join-Path $state "columns.txt"), (@(
                "alembic_version|version_num|character varying|32", "team_state|kind|character varying|16",
                "team_state|key|character varying|80", "team_state|doc|jsonb|", "team_state|updated_at|character varying|32",
                "wake_alarms|fires_at|timestamp with time zone|") -join "`n") + "`n")
}

function Get-TreeState {
    # name + sha256 of every file under the fake host: what "it changed nothing" is compared on
    return (@(Get-ChildItem -LiteralPath $hostRoot -Recurse -File -Force | Sort-Object -Property FullName | ForEach-Object {
                "$($_.FullName.Substring($hostRoot.Length))=$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)" }) -join ";")
}

function Invoke-Snapshot {
    param([hashtable]$Env = @{}, [string]$Script = $hostScript)
    foreach ($f in "calls.log", "trace.log", "stderr.txt", "flock.n") { Remove-Item -LiteralPath (Join-Path $state $f) -Force -ErrorAction SilentlyContinue }
    $before = Get-TreeState
    # The way the collector runs it: the script on stdin of 'bash -s'. xtrace goes to its own
    # descriptor, stderr to a file: stdout is the document and nothing else.
    $cmd = "PAGENTOS_BASE='$(& $u $base)' PAGENTOS_EDGE_DIR='$(& $u $edge)' PAGENTOS_RECOVERY_ROOT='$(& $u $recovery)' " +
           "PAGENTOS_REBOOT_REQUIRED='$(& $u (Join-Path $run 'reboot-required'))' PAGENTOS_LOCK_STEP_S=0 " +
           "FAKE_STATE='$(& $u $state)' PAGENTOS_DECOY_SECRET='hunter2-env' " +
           (($Env.GetEnumerator() | ForEach-Object { "$($_.Key)='$($_.Value)' " }) -join "") +
           "PATH='$(& $posix $fakeBin):'`"`$PATH`" PS4='+$traceMarker ' BASH_XTRACEFD=7 bash -x -s < '$(& $u $Script)' 7> '$(& $u (Join-Path $state 'trace.log'))' 2> '$(& $u (Join-Path $state 'stderr.txt'))'"
    $out = & $bash -c (ConvertTo-NativeCallArgument -Value $cmd) | Out-String
    $exit = $LASTEXITCODE
    $read = { param($name) $p = Join-Path $state $name; if (Test-Path -LiteralPath $p) { @(Get-Content -LiteralPath $p) } else { @() } }
    $doc = $null
    try { if ($out.Trim()) { $doc = $out | ConvertFrom-Json } } catch { $doc = $null }
    return [pscustomobject]@{
        Output = $out; Exit = $exit; Doc = $doc; Calls = @(& $read "calls.log"); Trace = @(& $read "trace.log")
        Stderr = (@(& $read "stderr.txt") -join "`n"); Unchanged = ($before -eq (Get-TreeState))
    }
}

# ---- the allow-list ---------------------------------------------------------------------
# Command words bash may trace. Builtins and keywords change nothing outside the shell (the
# script holds no redirection, see the lint below); the functions are the script's own; the
# externals are checked argument by argument in Test-ReadOnlyCall.
$allowedBuiltins = @("[", "[[", "((", "echo", "printf", "local", "read", "true", ":", "return", "exit", "set", "for", "case", "continue", "break")
$allowedFunctions = @("say", "fail", "json_str", "marker_sha", "read_colour", "sample_lock")
$allowedExternals = @("docker", "psql", "systemctl", "flock", "cat", "stat", "uname", "sleep")

# This machine's culture is tr-TR, where a case-insensitive match folds 'I' to the dotless
# 'i': '(?i)INTO' does not match 'into', and '[A-Za-z]' does not match 'I'. Every pattern
# that decides what is allowed is therefore matched culture-invariantly, or case-sensitively.
$rxInvariant = [System.Text.RegularExpressions.RegexOptions]"IgnoreCase, CultureInvariant"

function Test-ReadOnlyStatement {
    # "" when the statement is one SELECT over exactly one information_schema relation.
    param([string]$Statement)
    $bare = [regex]::Replace($Statement, "'[^']*'", "''")
    if (-not [regex]::IsMatch($bare, '^\s*SELECT\s', $rxInvariant)) { return "the statement does not start with SELECT" }
    if ($bare -match '[;()]') { return "the statement holds ';' or a parenthesis (a second statement, a subquery or a function call)" }
    $keyword = [regex]::Match($bare, '\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|COPY|GRANT|REVOKE|CALL|DO|INTO|SET|LOCK|VACUUM|NOTIFY|EXECUTE|JOIN|UNION)\b', $rxInvariant)
    if ($keyword.Success) { return "the statement holds the keyword $($keyword.Value)" }
    if ([regex]::Matches($bare, '\bFROM\b', $rxInvariant).Count -ne 1) { return "the statement does not hold exactly one FROM" }
    if (-not [regex]::IsMatch($bare, '\bFROM\s+information_schema\.[a-z_]+\s*(\s(WHERE|ORDER)\b.*)?$', $rxInvariant)) { return "the statement reads something that is not one information_schema relation" }
    return ""
}

function Test-ReadOnlyCall {
    # "" when the call is on the read-only allow-list, otherwise why it is not.
    param([string]$Name, [string[]]$CallArgs = @())
    $a = @($CallArgs)
    $first = if ($a.Count -gt 0) { $a[0] } else { "" }
    switch ($Name) {
        "docker" {
            if ($first -eq "ps") {
                # Names and states and nothing else: '{{.Command}}' is a command line, and
                # 'docker inspect' (not allowed at all) prints '.Config.Env'.
                $formats = 0
                for ($i = 1; $i -lt $a.Count; $i++) {
                    if ($a[$i] -cin @("-a", "--all")) { continue }
                    if ($a[$i] -cne "--format" -or ($i + 1) -ge $a.Count) { return "docker ps with '$($a[$i])'" }
                    $i++; $formats++
                    if ([regex]::Replace($a[$i], '\{\{\.(Names|State)\}\}', '') -match '[{}]') { return "docker ps with a format that is not names and states only: $($a[$i])" }
                }
                if ($formats -ne 1) { return "docker ps without '--format' (names and states only)" }
                return ""
            }
            if ($first -eq "exec") {
                if ($a.Count -lt 5 -or $a[1] -ne "-e" -or $a[2] -ne "PGOPTIONS=-c default_transaction_read_only=on" -or $a[3] -cnotmatch '^[A-Za-z0-9_.-]+$' -or $a[4] -ne "psql") {
                    return "docker exec of something other than 'psql' in a read-only session"
                }
                return (Test-ReadOnlyCall -Name "psql" -CallArgs @($a | Select-Object -Skip 5))
            }
            return "docker $first is not a read"
        }
        "psql" {
            $i = 0
            while ($i -lt $a.Count -and ($a[$i] -eq "-U" -or $a[$i] -eq "-d")) { $i += 2 }
            if ($a.Count -ne ($i + 2) -or $a[$i] -cne "-Atc") { return "psql is not '[-U user] [-d db] -Atc <one statement>'" }
            return (Test-ReadOnlyStatement -Statement $a[$i + 1])
        }
        "systemctl" { if ($first -in @("list-timers", "is-active")) { return "" }; return "systemctl $first is not list-timers / is-active" }
        "flock" {
            if ($a.Count -eq 3 -and $a[0] -eq "-n" -and $a[1] -eq $lockFile -and $a[2] -eq "true") { return "" }
            return "flock is not '-n <the operation lock> true'"
        }
        { $_ -in @("cat", "stat") } {
            $files = @($a | Where-Object { $_ -notmatch '^-' -and $_ -notmatch '^%' })
            $bad = @($files | Where-Object { $_ -notin $namedFiles })
            if ($files.Count -eq 0 -or $bad.Count -gt 0) { return "$Name of a file that is not a named marker: $($bad -join ' ')" }
            return ""
        }
        "uname" { if (@($a | Where-Object { $_ -cnotmatch '^-[a-z]+$' }).Count -eq 0) { return "" }; return "uname with an operand" }
        "sleep" { if ($a.Count -eq 1 -and $a[0] -match '^\d+$') { return "" }; return "sleep with something other than seconds" }
    }
    return "'$Name' is not on the allow-list"
}

function Test-TracedCommand {
    # "" when one traced command is allowed, otherwise why it is not. The trace is the record,
    # so beyond the command word this refuses whatever would end the record: any 'set' but the
    # script's one, and any assignment to a variable that is not the script's own (its names
    # are lower-case; PS4, BASH_XTRACEFD and PATH are not) - plain, or through local / read /
    # for / printf -v.
    param([string]$Text)
    $tokens = @($Text.Trim() -split '\s+' | ForEach-Object { $_.Trim("'") })
    $word = $tokens[0]
    $own = '^[a-z_][a-z0-9_]*$'
    if ($word -cmatch '^([A-Za-z_][A-Za-z0-9_]*)\+?=') {
        if ($Matches[1] -cmatch $own -or $Matches[1] -ceq "IFS") { return "" }
        return "an assignment to '$($Matches[1])', which is not a variable of the script"
    }
    if ($word -cnotin $allowedBuiltins -and $word -cnotin $allowedFunctions -and $word -cnotin $allowedExternals) { return "traced command '$word' is not on the allow-list" }
    $names = @()
    if ($word -ceq "set" -and $Text.Trim() -cne "set -eu -o pipefail") { return "traced command 'set' is not 'set -eu -o pipefail' (it could switch the recorder off)" }
    if ($word -ceq "printf" -and $tokens.Count -gt 1 -and $tokens[1] -ceq "-v") { return "traced command 'printf -v' assigns a variable" }
    if ($word -ceq "for") { $names = @($tokens | Select-Object -Skip 1 -First 1) }
    if ($word -cin @("local", "read")) { $names = @($tokens | Select-Object -Skip 1 | Where-Object { $_ -notmatch '^-' } | ForEach-Object { ($_ -split '=', 2)[0] }) }
    $foreign = @($names | Where-Object { $_ -cnotmatch $own })
    if ($foreign.Count -gt 0) { return "traced command '$word' names '$($foreign[0])', which is not a variable of the script" }
    return ""
}

function Get-SourceViolations {
    # What the trace cannot show, refused in the text of the script instead: a redirection
    # (only '<<<', '>&2' and '2>/dev/null' are allowed), and the names of the recorder's own
    # variables anywhere at all - an arithmetic expansion can assign one and is traced as its
    # value only.
    param([string[]]$Lines)
    $found = New-Object System.Collections.ArrayList
    foreach ($line in @($Lines | Where-Object { $_ -notmatch '^\s*#' })) {
        if (((($line -replace '<<<', '') -replace '2>/dev/null', '') -replace '>&2', '') -match '[<>]') { [void]$found.Add("a redirection the trace cannot see: $line") }
        if ($line -cmatch '\b(PS4|BASH_XTRACEFD|PATH|SHELLOPTS|BASH_ENV|xtrace)\b') { [void]$found.Add("the recorder's '$($Matches[1])' is named: $line") }
    }
    return @($found.ToArray())
}

function Get-RunViolations {
    # Every reason one run broke the allow-list: the traced command words, the fakes' exact
    # arguments, and every path under the fake host that the trace mentions.
    param([string[]]$Trace, [string[]]$Calls)
    $found = New-Object System.Collections.ArrayList
    $words = 0
    foreach ($line in @($Trace)) {
        if ($line -notmatch ('^\++' + [regex]::Escape($traceMarker) + ' (.*)$')) { continue }   # the rest of a quoted, multi-line argument
        $words++
        $text = $Matches[1]
        $why = Test-TracedCommand -Text $text
        if ($why) { [void]$found.Add("$($why): $text") }
    }
    if ($words -eq 0) { [void]$found.Add("the trace is empty: nothing was recorded") }
    foreach ($call in @($Calls)) {
        $parts = @($call -split $sep)
        $why = Test-ReadOnlyCall -Name $parts[0] -CallArgs @($parts | Select-Object -Skip 1)
        if ($why) { [void]$found.Add("$($parts -join ' '): $why") }
    }
    $rootText = & $u $hostRoot
    foreach ($m in [regex]::Matches((@($Trace) -join "`n"), [regex]::Escape($rootText) + '[^\s''"]*')) {
        if ($m.Value -notin $namedFiles -and $m.Value -notin $namedDirs) { [void]$found.Add("a path that is not a named marker: $($m.Value)") }
    }
    return @($found.ToArray() | Select-Object -Unique)
}

function Invoke-Collector {
    param([string[]]$Arguments, [hashtable]$Env = @{})
    $set = @{
        PAGENTOS_BASE = (& $u $base); PAGENTOS_EDGE_DIR = (& $u $edge); PAGENTOS_RECOVERY_ROOT = (& $u $recovery)
        PAGENTOS_REBOOT_REQUIRED = (& $u (Join-Path $run 'reboot-required')); PAGENTOS_LOCK_STEP_S = "0"; PAGENTOS_LOCK_SAMPLES = "6"
        FAKE_STATE = (& $u $state); FAKE_BIN = (& $posix $fakeBin)
    }
    foreach ($k in $Env.Keys) { $set[$k] = $Env[$k] }
    foreach ($f in "calls.log", "flock.n", "ssh-args.log", "ssh-stdin.sh") { Remove-Item -LiteralPath (Join-Path $state $f) -Force -ErrorAction SilentlyContinue }
    $saved = @{}
    foreach ($k in $set.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $set[$k]) }
    try { $out = & $powershellExe -NoProfile -ExecutionPolicy Bypass -File $collector @Arguments | Out-String; $exit = $LASTEXITCODE }
    finally { foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) } }
    return [pscustomobject]@{ Output = $out; Exit = $exit }
}
function Get-Sha256 { param([string]$Path) if (-not (Test-Path -LiteralPath $Path)) { return "absent" }; return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash }

try {
    if (-not (Test-Path $bash)) { Write-Host "  SKIP  host snapshot tests: Git Bash not found at $bash" }
    else {
        Write-Host "the allow-list refuses what it must (the checker, before anything is run through it)"
        $pg = @("exec", "-e", "PGOPTIONS=-c default_transaction_read_only=on", "pagentos-prod-postgres", "psql", "-U", "pagentos", "-d", "pagentos_prod")
        $good = "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public' ORDER BY table_name"
        Assert-True ((Test-ReadOnlyCall -Name "docker" -CallArgs ($pg + @("-Atc", $good))) -eq "") "accepted: docker exec <postgres> psql -Atc with one SELECT over information_schema.columns"
        $refused = @(
            @{ Why = "an UPDATE through docker exec psql -c"; Name = "docker"; A = ($pg + @("-c", "UPDATE team_state SET updated_at = 'x'")) },
            @{ Why = "an UPDATE dressed as -Atc"; Name = "docker"; A = ($pg + @("-Atc", "UPDATE team_state SET updated_at = 'x'")) },
            @{ Why = "a SELECT from an application table (team_state)"; Name = "docker"; A = ($pg + @("-Atc", "SELECT key FROM team_state")) },
            @{ Why = "information_schema joined onto an application table"; Name = "docker"; A = ($pg + @("-Atc", "SELECT c.table_name FROM information_schema.columns c JOIN team_state t ON true")) },
            @{ Why = "an application table beside information_schema (comma)"; Name = "docker"; A = ($pg + @("-Atc", "SELECT 1 FROM information_schema.columns, team_state")) },
            @{ Why = "an application table in a subquery"; Name = "docker"; A = ($pg + @("-Atc", "SELECT table_name FROM information_schema.columns WHERE table_name IN (SELECT key FROM team_state)")) },
            @{ Why = "SELECT ... INTO a new table, in lower case (tr-TR folds I: a culture-sensitive match misses 'into')"; Name = "docker"; A = ($pg + @("-Atc", "select table_name into scratch from information_schema.columns")) },
            @{ Why = "a lower-case insert"; Name = "docker"; A = ($pg + @("-Atc", "insert into team_state select 1 from information_schema.columns")) },
            @{ Why = "a second statement after the SELECT"; Name = "docker"; A = ($pg + @("-Atc", "$good; DELETE FROM team_state")) },
            @{ Why = "a psql script file (-f)"; Name = "docker"; A = ($pg + @("-f", "/tmp/x.sql")) },
            @{ Why = "docker exec without the read-only session"; Name = "docker"; A = @("exec", "pagentos-prod-postgres", "psql", "-Atc", $good) },
            @{ Why = "docker exec of a shell"; Name = "docker"; A = @("exec", "pagentos-prod-api-green", "sh", "-c", "env") },
            @{ Why = "docker rm"; Name = "docker"; A = @("rm", "pagentos-prod-api") },
            @{ Why = "docker restart"; Name = "docker"; A = @("restart", "pagentos-prod-edge") },
            @{ Why = "psql on the host with an UPDATE"; Name = "psql"; A = @("-c", "UPDATE team_state SET key = 'x'") },
            @{ Why = "systemctl stop"; Name = "systemctl"; A = @("stop", "pagentos-bluegreen-reconcile.timer") },
            @{ Why = "flock that waits"; Name = "flock"; A = @("-w", "45", $lockFile, "true") },
            @{ Why = "flock that runs something under the lock"; Name = "flock"; A = @("-n", $lockFile, "rm", "-f", "x") },
            @{ Why = "flock on another file (it would create it)"; Name = "flock"; A = @("-n", (& $u (Join-Path $base "x.lock")), "true") },
            @{ Why = "cat of the env file"; Name = "cat"; A = @((& $u (Join-Path $base ".env"))) },
            @{ Why = "a command that is not on the list at all (rm)"; Name = "rm"; A = @("-f", "x") }
        )
        foreach ($r in $refused) { Assert-True ((Test-ReadOnlyCall -Name $r.Name -CallArgs $r.A) -ne "") "refused: $($r.Why)" }
        $v = @(Get-RunViolations -Trace @("+$traceMarker base=x", "+$traceMarker curl -fsS http://127.0.0.1:8001/v1/system/health") -Calls @())
        Assert-True ($v.Count -eq 1 -and $v[0] -match "curl") "refused: a traced command word outside the list (curl), though no fake recorded it"
        $v = @(Get-RunViolations -Trace @("+$traceMarker [ -f $(& $u (Join-Path $base 'app/.env')) ]") -Calls @())
        Assert-True ($v.Count -eq 1 -and $v[0] -match "not a named marker") "refused: a path under the base that is not a named marker, even in a builtin test"
        # docker: 'ps' with name and state only. 'inspect' is not a read this script needs, and
        # '{{json .Config.Env}}' would put every production secret on the host's stderr.
        Assert-True ((Test-ReadOnlyCall -Name "docker" -CallArgs @("ps", "-a", "--format", "{{.Names}}|{{.State}}")) -eq "") "accepted: docker ps -a with the names and the states"
        $refusedDocker = @(
            @{ Why = "docker inspect of a container's environment ('.Config.Env')"; A = @("inspect", "--format", "{{json .Config.Env}}", "pagentos-prod-api-green") },
            @{ Why = "docker inspect at all, even of a state"; A = @("inspect", "--format", "{{.State.Status}}", "pagentos-prod-api-green") },
            @{ Why = "docker ps with a field that is not a name or a state (the command line)"; A = @("ps", "-a", "--no-trunc", "--format", "{{.Command}}") },
            @{ Why = "docker ps with every field as JSON"; A = @("ps", "-a", "--format", "{{json .}}") }
        )
        foreach ($r in $refusedDocker) { Assert-True ((Test-ReadOnlyCall -Name "docker" -CallArgs $r.A) -ne "") "refused: $($r.Why)" }
        # The record is bash's own trace, so nothing the script runs may switch it off, rename
        # its marker, send it elsewhere, or step around the fakes that log the arguments.
        $tracedOk = @("set -eu -o pipefail", "IFS='|'", "lock_held=1", "local s=b v", "read -r name state", "for word in Thu UTC pagentos-restore-drill.timer", "printf '\`"%s\`"' x")
        foreach ($line in $tracedOk) { Assert-True (@(Get-RunViolations -Trace @("+$traceMarker $line") -Calls @()).Count -eq 0) "accepted in the trace: $line" }
        $tracedRefused = @(
            @{ Why = "set +x: the recorder is switched off, what follows is not recorded"; Line = "set +x" },
            @{ Why = "set +o xtrace"; Line = "set +o xtrace" },
            @{ Why = "set -x (only ever needed after the recorder was switched off)"; Line = "set -x" },
            @{ Why = "set with the right options and one more"; Line = "set -eu -o pipefail +x" },
            @{ Why = "PS4 assigned: the trace's marker changes and later lines are not recognised"; Line = "PS4='+ '" },
            @{ Why = "BASH_XTRACEFD assigned: the trace goes somewhere else"; Line = "BASH_XTRACEFD=2" },
            @{ Why = "PATH assigned: the fakes that record the arguments are stepped around"; Line = "PATH=/usr/bin" },
            @{ Why = "PATH assigned through local"; Line = "local PATH=/usr/bin" },
            @{ Why = "PS4 assigned through read"; Line = "read -r PS4" },
            @{ Why = "PS4 assigned through printf -v"; Line = "printf -v PS4 %s x" },
            @{ Why = "PATH assigned as a loop variable"; Line = "for PATH in /usr/bin" }
        )
        foreach ($r in $tracedRefused) { Assert-True (@(Get-RunViolations -Trace @("+$traceMarker $($r.Line)") -Calls @()).Count -eq 1) "refused in the trace: $($r.Why)" }

        Write-Host "the script holds no redirection xtrace cannot see"
        $code = @(Get-Content -LiteralPath $hostScript -ErrorAction SilentlyContinue | Where-Object { $_ -notmatch '^\s*#' })
        $inSource = @(Get-SourceViolations -Lines $code)
        Assert-True ($code.Count -gt 20 -and $inSource.Count -eq 0) "no '<' or '>' in the script beyond '<<<', '>&2' and '2>/dev/null', and the recorder's variables are not named ($($inSource.Count) line(s): $(@($inSource | Select-Object -First 2) -join ' // '))"
        $sourceRefused = @(
            @{ Why = "a file read with '<'"; Line = 'v=$(<"$base/.env")' },
            @{ Why = "a file written with '>'"; Line = 'echo x > "$base/RELEASE"' },
            @{ Why = "the trace sent elsewhere inside an arithmetic expansion (traced as ': 2')"; Line = ': $((BASH_XTRACEFD = 2))' },
            @{ Why = "the fakes stepped around through a default expansion"; Line = ': "${PATH:=/usr/bin}"' },
            @{ Why = "shopt -u -o xtrace"; Line = 'shopt -u -o xtrace' }
        )
        foreach ($r in $sourceRefused) { Assert-True (@(Get-SourceViolations -Lines @($r.Line)).Count -eq 1) "refused in the source: $($r.Why)" }
        Assert-True (@(Get-SourceViolations -Lines @('say "x" >&2', 'done <<< "$listing"', 'if flock -n "$lock_file" true 2>/dev/null; then run=0', '# PATH in a comment')).Count -eq 0) "accepted in the source: '>&2', '<<<', '2>/dev/null', a comment"
        Assert-True (@($code | Where-Object { $_ -match '^set -eu -o pipefail$' }).Count -eq 1) "set -eu -o pipefail"

        Write-Host "host-snapshot.sh under Git Bash, fakes: GREEN serves, the lock is held 2 of 60"
        Reset-Host -Colour green
        $s = Invoke-Snapshot -Env @{ FAKE_FLOCK_HELD = "10 11" }
        if ($s.Exit -ne 0 -or $null -eq $s.Doc -or $env:PAGENTOS_SNAPSHOT_VERBOSE) { Write-Host $s.Output; Write-Host $s.Stderr }
        $d = $s.Doc
        Assert-True ($s.Exit -eq 0 -and $null -ne $d -and $s.Output.TrimStart().StartsWith("{") -and $s.Output.TrimEnd().EndsWith("}")) "exit 0 and stdout is ONE JSON document"
        $raw = Join-Path $state "raw.json"
        [IO.File]::WriteAllText($raw, $s.Output)
        $val = Invoke-Collector -Arguments @("-ValidateFile", $raw, "-Raw")
        if ($val.Exit -ne 0) { Write-Host $val.Output }
        Assert-True ($val.Exit -eq 0) "the collector's schema holds for the document"
        Assert-True ($s.Stderr -match "snapshot: " -and $s.Output -notmatch "snapshot: ") "status lines go to stderr, not into the document"
        $violations = @(Get-RunViolations -Trace $s.Trace -Calls $s.Calls)
        foreach ($x in $violations) { Write-Host "        $x" -ForegroundColor Red }
        Assert-True ($violations.Count -eq 0) "every command it ran is on the read-only allow-list ($($s.Calls.Count) recorded calls, $($s.Trace.Count) trace lines)"
        $traced = @($s.Trace | Where-Object { $_ -match ('^\++' + [regex]::Escape($traceMarker) + ' ') })
        Assert-True ($traced.Count -gt 0 -and $traced[-1].EndsWith("echo 'snapshot: done'") -and $s.Stderr -notmatch [regex]::Escape($traceMarker)) "the recorder ran to the script's last command, and none of its lines went to stderr instead"
        Assert-True (@($s.Calls | Where-Object { $_ -match "^docker${sep}exec" }).Count -eq 1 -and @($s.Calls | Where-Object { $_ -match "information_schema\.columns" }).Count -eq 1) "the schema is read once, from information_schema.columns"
        Assert-True ($s.Unchanged) "the fake host is byte-for-byte the same after the run"
        Assert-True (($s.Output + $s.Stderr) -notmatch "hunter2") "neither the env file's content nor an environment value is in the output"
        if ($null -ne $d) {
            Assert-True ($d.serving_colour -eq "green" -and $d.host.kernel -eq "6.8.0-138-generic" -and $d.host.reboot_required -eq $true) "serving colour green, the kernel, reboot-required present"
            $names = @($d.containers | ForEach-Object { "$($_.name)=$($_.state)" })
            Assert-True ($names.Count -eq 9 -and $names -contains "pagentos-prod-api-green=running" -and $names -contains "pagentos-prod-api-blue=exited") "nine containers with their states; api-blue is listed as exited"
            Assert-True ($d.markers.release -eq $sha -and $d.markers.last_known_good -eq $lkg -and $d.markers.recovery_pin -eq $sha) "RELEASE, LAST_KNOWN_GOOD and the recovery pin as 40 hex"
            $timers = @($d.timers | ForEach-Object { "$($_.name)=$($_.active)" })
            Assert-True ($timers.Count -eq 3 -and $timers -contains "pagentos-bluegreen-reconcile.timer=active" -and $timers -contains "pagentos-restore-drill.timer=inactive") "the pagentos-* timers with their state"
            Assert-True ($d.operation_lock.present -eq $true -and $d.operation_lock.samples -eq 60 -and $d.operation_lock.held -eq 2 -and $d.operation_lock.longest_run -eq 2) "sixty samples of the operation lock: held 2, longest run 2"
            Assert-True (@($s.Calls | Where-Object { $_ -match "^flock" }).Count -eq 60) "sixty flock -n probes, one per sample"
            $col = @($d.columns | Where-Object { $_.table -eq "team_state" -and $_.column -eq "updated_at" })
            $doc = @($d.columns | Where-Object { $_.table -eq "team_state" -and $_.column -eq "doc" })
            Assert-True (@($d.columns).Count -eq 6 -and $col.Count -eq 1 -and $col[0].data_type -eq "character varying" -and $col[0].character_maximum_length -eq 32 -and $doc.Count -eq 1 -and $null -eq $doc[0].character_maximum_length) "columns: team_state.updated_at character varying 32; a column without a length is null"
        }

        Write-Host "host-snapshot.sh: BLUE serves, markers missing or damaged, two separate holds"
        Reset-Host -Bare
        $s = Invoke-Snapshot -Env @{ FAKE_FLOCK_HELD = "5 20 21 22"; PAGENTOS_LOCK_SAMPLES = "24" }
        $d = $s.Doc
        if ($s.Exit -ne 0 -or $null -eq $d) { Write-Host $s.Output; Write-Host $s.Stderr }
        Assert-True ($s.Exit -eq 0 -and $null -ne $d -and $d.serving_colour -eq "blue" -and $d.host.reboot_required -eq $false) "the marker's 'BLUE' is read as blue; reboot-required absent is false"
        Assert-True ($null -ne $d -and $d.markers.release -eq "missing" -and $d.markers.recovery_pin -eq "missing" -and $d.markers.last_known_good -eq "invalid" -and $s.Output -notmatch "not a sha") "a missing marker is 'missing'; one that is not 40 hex is 'invalid' and its content is not printed"
        Assert-True ($null -ne $d -and $d.operation_lock.samples -eq 24 -and $d.operation_lock.held -eq 4 -and $d.operation_lock.longest_run -eq 3) "held 4 of 24 in two holds: the longest run is 3, not 4"
        Assert-True (@(Get-RunViolations -Trace $s.Trace -Calls $s.Calls).Count -eq 0 -and $s.Unchanged) "allow-list holds and nothing changed on this host too"

        Write-Host "host-snapshot.sh: no lock file on the host"
        Reset-Host -NoLock
        $s = Invoke-Snapshot -Env @{ PAGENTOS_LOCK_SAMPLES = "5" }
        $d = $s.Doc
        Assert-True ($s.Exit -eq 0 -and $null -ne $d -and $d.operation_lock.present -eq $false -and $d.operation_lock.held -eq 0 -and $d.operation_lock.samples -eq 0) "a missing lock file is reported as not present, with no samples"
        Assert-True (@($s.Calls | Where-Object { $_ -match "^flock" }).Count -eq 0 -and -not (Test-Path -LiteralPath (Join-Path $base ".bluegreen-operation.lock")) -and $s.Unchanged) "flock is not called for a missing lock file (it would create it)"

        Write-Host "host-snapshot.sh: a step fails"
        Reset-Host
        $s = Invoke-Snapshot -Env @{ FAKE_DOCKER_FAIL = "1"; PAGENTOS_LOCK_SAMPLES = "2" }
        Assert-True ($s.Exit -ne 0 -and $s.Output.Trim() -eq "" -and $s.Stderr -match "SNAPSHOT FAILED") "a failing docker: non-zero exit, a named failure on stderr and NO document on stdout"

        # The two holes an inspector walked through (cycle d20261001), each as a scratch copy of
        # the script with one line added after 'set -eu -o pipefail', run the same way.
        Write-Host "host-snapshot.sh with a line added: the allow-list is what goes RED"
        $scriptText = [IO.File]::ReadAllText($hostScript) -replace "`r`n", "`n"
        $anchor = "set -eu -o pipefail`n"
        $added = @(
            @{ Why = "'set +x' around a command hides it from the recorder"; Line = "set +x; : a command nobody recorded; set -x"; Match = "traced command 'set'" },
            @{ Why = "'docker inspect' of '.Config.Env' prints a container's environment"; Line = 'echo "snapshot: $(docker inspect --format "{{json .Config.Env}}" pagentos-prod-api-green)" >&2'; Match = "docker inspect is not a read" }
        )
        foreach ($m in $added) {
            Reset-Host -Colour green
            $scratch = Join-Path $state "host-snapshot-added.sh"
            [IO.File]::WriteAllText($scratch, $scriptText.Replace($anchor, $anchor + $m.Line + "`n"))
            $s = Invoke-Snapshot -Env @{ PAGENTOS_LOCK_SAMPLES = "2" } -Script $scratch
            $violations = @(Get-RunViolations -Trace $s.Trace -Calls $s.Calls)
            Assert-True ($scriptText.Contains($anchor) -and $s.Exit -eq 0 -and @($violations | Where-Object { $_.Contains($m.Match) }).Count -ge 1) "refused when run: $($m.Why) ($($violations.Count) violation(s))"
        }

        Write-Host "the fixture and the collector (collect-host-snapshot.ps1, fake ssh)"
        $val = Invoke-Collector -Arguments @("-ValidateFile", $fixture)
        if ($val.Exit -ne 0) { Write-Host $val.Output }
        Assert-True ($val.Exit -eq 0) "the committed fixture holds the collector's schema"
        $fixtureText = [IO.File]::ReadAllText($fixture)
        $broken = Join-Path $state "broken.json"
        $breaks = @(
            @{ Why = "a member the schema does not know (an environment dump)"; From = '"schema_version": 1,'; To = '"schema_version": 1, "environment": {"PAGENTOS_OWNER_TOKEN": "x"},' },
            @{ Why = "held more often than sampled"; From = '"held": 2,'; To = '"held": 61,' },
            @{ Why = "a serving colour that is not a colour"; From = '"serving_colour": "green"'; To = '"serving_colour": "teal"' },
            @{ Why = "a width that is not a number"; From = '"character_maximum_length": 32'; To = '"character_maximum_length": "32"' },
            @{ Why = "no collected_at"; From = '"collected_at": "2026-10-01",'; To = '' }
        )
        foreach ($b in $breaks) {
            [IO.File]::WriteAllText($broken, $fixtureText.Replace($b.From, $b.To))
            $val = Invoke-Collector -Arguments @("-ValidateFile", $broken)
            Assert-True ($fixtureText.Contains($b.From) -and $val.Exit -eq 3) "the schema refuses (3): $($b.Why)"
        }

        Reset-Host -Colour green
        $target = Join-Path $state "collected.json"
        $c = Invoke-Collector -Arguments @("-SshExe", $fakeSsh, "-CloudHost", "root@fake-host", "-OutFile", $target) -Env @{ FAKE_FLOCK_HELD = "3" }
        if ($c.Exit -ne 0 -or $env:PAGENTOS_SNAPSHOT_VERBOSE) { Write-Host $c.Output }
        $written = if (Test-Path -LiteralPath $target) { [IO.File]::ReadAllText($target) } else { "" }
        $w = $null
        try { if ($written) { $w = $written | ConvertFrom-Json } } catch { $w = $null }
        Assert-True ($c.Exit -eq 0 -and $null -ne $w -and $w.serving_colour -eq "green" -and $w.operation_lock.held -eq 1 -and @($w.columns).Count -eq 6) "with a fake ssh the collector writes the fixture from the script's own output"
        Assert-True ($null -ne $w -and "$($w.collected_at)" -match '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$' -and "$($w.source)" -match "host-snapshot.sh" -and "$($w.source)" -notmatch "hand-written") "the fixture carries collected_at and a source that is not 'hand-written'"
        Assert-True ($written -and $written -notmatch "`r" -and $written -notmatch "Permanently added") "LF only, and ssh's stderr is not in the file"
        $sshArgs = @(Get-Content -LiteralPath (Join-Path $state "ssh-args.log") -ErrorAction SilentlyContinue)
        Assert-True ($sshArgs.Count -eq 1 -and $sshArgs[0] -match "-o BatchMode=yes" -and $sshArgs[0] -match "root@fake-host bash -s$") "one ssh call: BatchMode, the host, 'bash -s'"
        $sent = Join-Path $state "ssh-stdin.sh"
        $lf = Join-Path $state "script-lf.sh"
        [IO.File]::WriteAllText($lf, ([IO.File]::ReadAllText($hostScript) -replace "`r`n", "`n"))
        Assert-True ((Test-Path -LiteralPath $sent) -and (Get-Sha256 $sent) -eq (Get-Sha256 $lf)) "the script reached the host's stdin byte for byte (LF)"
        $val = Invoke-Collector -Arguments @("-ValidateFile", $target)
        Assert-True ($val.Exit -eq 0) "what the collector wrote holds its own schema"

        $beforeHash = Get-Sha256 $target
        $c = Invoke-Collector -Arguments @("-SshExe", $fakeSsh, "-CloudHost", "root@fake-host", "-OutFile", $target) -Env @{ FAKE_SSH_FAIL = "1" }
        Assert-True ($c.Exit -eq 4 -and $c.Output -match "nothing was changed" -and (Get-Sha256 $target) -eq $beforeHash) "with an unreachable ssh: exit 4, says so, the existing fixture is untouched"
        $absent = Join-Path $state "never.json"
        $c = Invoke-Collector -Arguments @("-SshExe", $fakeSsh, "-CloudHost", "root@fake-host", "-OutFile", $absent) -Env @{ FAKE_SSH_FAIL = "1" }
        Assert-True ($c.Exit -eq 4 -and @(Get-ChildItem -LiteralPath $state -Filter "never.json*").Count -eq 0) "with an unreachable ssh and no fixture yet: exit 4 and no file, not even a partial one"
        $c = Invoke-Collector -Arguments @("-SshExe", (Join-Path $fakeBin "no-such-ssh.exe"), "-CloudHost", "root@fake-host", "-OutFile", $absent)
        Assert-True ($c.Exit -eq 4 -and @(Get-ChildItem -LiteralPath $state -Filter "never.json*").Count -eq 0) "with no ssh program at all: exit 4 and no file"
        $c = Invoke-Collector -Arguments @("-SshExe", $fakeSsh, "-CloudHost", "root@fake-host", "-OutFile", $target) -Env @{ FAKE_SSH_GARBAGE = "1" }
        Assert-True ($c.Exit -eq 3 -and (Get-Sha256 $target) -eq $beforeHash -and @(Get-ChildItem -LiteralPath $state -Filter "collected.json*").Count -eq 1) "a host that answers with a document the schema refuses: exit 3, the fixture is untouched"
    }
}
finally { Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue }
Write-Host ""
Write-Host "passed: $($script:Passes)  failed: $($script:Failures)"
if ($script:Failures -gt 0) { exit 1 }
