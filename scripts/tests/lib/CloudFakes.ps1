<#
.SYNOPSIS
    Test-only fakes for the cloud scripts: a REAL native ssh/scp stand-in (compiled here
    with Add-Type so 5.1 native-argument quoting is exercised for real) and a fake
    docker/curl pair for running the host-side bash transactions under Git Bash.
#>

Set-StrictMode -Version Latest

function New-NativeFakeSsh {
    <#
    .SYNOPSIS
        Compile a native .exe at $Directory\ssh.exe that records its parsed argv (one per
        line) to argv.txt, appends every call to calls.txt (separated by ---), records
        stdin verbatim to stdin.txt, prints $env:FAKE_SSH_STDOUT when set, and exits with
        $env:FAKE_SSH_EXIT (default 0). Returns the exe path.

        The stdin read is BOUNDED. On 2026-09-02 a debug run of the release driver left
        this stub alive for sixty-three hours: release-cloud-core.ps1 invoked it as scp.exe
        with stdin redirected but never closed, so Console.In.ReadToEnd() waited for an EOF
        that could not arrive, and the whole task tree hung behind it. A test double that
        can outlive its own run is an orphan generator, which the standing rule forbids.
        Override the cap with $env:FAKE_SSH_STDIN_TIMEOUT_MS.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Directory)
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    $exe = Join-Path $Directory "ssh.exe"
    $source = @'
using System;
using System.IO;
using System.Threading;
public static class FakeSsh
{
    // Read redirected stdin, but never wait forever for an EOF the caller may never send.
    // The reader runs on a background thread so it cannot hold the process open either:
    // if the cap expires we record what arrived and exit anyway.
    static string ReadStdinBounded()
    {
        if (!Console.IsInputRedirected) { return ""; }
        int capMs = 10000;
        string raw = Environment.GetEnvironmentVariable("FAKE_SSH_STDIN_TIMEOUT_MS");
        if (!string.IsNullOrEmpty(raw)) { int.TryParse(raw, out capMs); }
        string captured = "";
        Thread reader = new Thread(delegate() {
            try { captured = Console.In.ReadToEnd(); } catch (Exception) { }
        });
        reader.IsBackground = true;
        reader.Start();
        reader.Join(capMs);
        return captured;
    }

    public static int Main(string[] args)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string joined = string.Join("\n", args);
        File.WriteAllText(Path.Combine(dir, "argv.txt"), joined);
        File.AppendAllText(Path.Combine(dir, "calls.txt"), joined + "\n---\n");
        File.WriteAllText(Path.Combine(dir, "stdin.txt"), ReadStdinBounded());
        string stdout = Environment.GetEnvironmentVariable("FAKE_SSH_STDOUT");
        if (!string.IsNullOrEmpty(stdout)) { Console.Out.WriteLine(stdout); }
        // The same binary serves as scp.exe and ssh.exe; each has its own exit knob so a
        // test can make the HOST step fail without the upload failing first.
        string exe = Path.GetFileNameWithoutExtension(Environment.GetCommandLineArgs()[0]).ToLowerInvariant();
        string exit = Environment.GetEnvironmentVariable(exe.StartsWith("scp") ? "FAKE_SCP_EXIT" : "FAKE_SSH_EXIT");
        return string.IsNullOrEmpty(exit) ? 0 : int.Parse(exit);
    }
}
'@
    Add-Type -TypeDefinition $source -OutputAssembly $exe -OutputType ConsoleApplication | Out-Null
    return $exe
}

function New-FakeDockerAndCurl {
    <#
    .SYNOPSIS
        Write LF-terminated bash fakes `docker` and `curl` into $Directory. State lives in
        $FAKE_STATE: calls.log (every docker invocation) and the `recreated` marker, which
        only `docker compose ... up ...` creates - so `docker exec ... printenv` reports
        MISSING until the api workload was really recreated. Knobs: FAKE_WIRED=0 (compose
        does not wire the key), FAKE_NEVER_PRESENT=1 (recreate never exposes it),
        FAKE_PROVIDER_LISTED=0, FAKE_SMOKE_EXIT=n, FAKE_CONFIG_EXIT=n, FAKE_UP_EXIT=n.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Directory)
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    $docker = @(
        '#!/usr/bin/env bash',
        'echo "docker $*" >> "$FAKE_STATE/calls.log"',
        '# exec first: an in-container command such as "uv run ..." must never match the',
        '# compose " run " pattern below (it did, and hid a self-test failure).',
        'case "$*" in',
        '  exec\ *)',
        '    case "$*" in',
        '      *"echo PRESENT"*) if [ -f "$FAKE_STATE/recreated" ]; then echo PRESENT; else echo MISSING; fi;;',
        '      *"wc -c"*) echo 40;;',
        '      *"sha256sum"*) echo abcdef012345;;',
        '      *"--voice "*)',
        '        all="$*"; v="${all##*--voice }"; v="${v%% *}"',
        '        if [ "${FAKE_VOICE_ECHO_WRONG:-0}" = "1" ]; then echo "{\"voice\": \"other\"}"; else echo "{\"voice\": \"$v\"}"; fi;;',
        '      *) if [ -n "${FAKE_SMOKE_EXIT:-}" ]; then echo "vendor refused (Authorization: Bearer nope)" >&2; exit "$FAKE_SMOKE_EXIT"; fi; echo ok;;',
        '    esac; exit 0;;',
        '# FAKE_BLUEGREEN=1: one colour is running behind the edge, so the installer must',
        '# NOT try to recreate the single-container "api" service (it binds 8001 directly,',
        '# the port the edge already owns).',
        '  ps\ *) if [ "${FAKE_BLUEGREEN:-0}" = "1" ]; then case "$*" in *api-green*) echo pagentos-prod-api-green;; esac; fi; exit 0;;',
        '  compose*" config -q") exit "${FAKE_CONFIG_EXIT:-0}";;',
        '  compose*" config")',
        '    if [ "${FAKE_WIRED:-1}" = "1" ]; then',
        '      printf "services:\n  api:\n    environment:\n      PAGENTOS_VOICE_OPENAI_API_KEY: x\n      PAGENTOS_TEST_PROVIDER_KEY: y\n"',
        '    else',
        '      printf "services:\n  api:\n    environment: {}\n"',
        '    fi; exit 0;;',
        '  compose*" up "*)',
        '    if [ -n "${FAKE_UP_EXIT:-}" ]; then exit "$FAKE_UP_EXIT"; fi',
        '    if [ "${FAKE_NEVER_PRESENT:-0}" != "1" ]; then touch "$FAKE_STATE/recreated"; fi; exit 0;;',
        '  compose*" build "*|compose*" run "*|tag\ *) exit 0;;',
        'esac',
        'exit 0'
    )
    $curl = @(
        '#!/usr/bin/env bash',
        'if [ -f "$FAKE_STATE/recreated" ] && [ "${FAKE_PROVIDER_LISTED:-1}" = "1" ]; then',
        '  printf "{\"status\":\"ok\",\"checks\":{\"voice_realtime\":{\"contract_version\":%s,\"providers\":[\"openai-realtime\",\"simulated\"]}}}" "${FAKE_CONTRACT_VERSION:-2}"',
        'else',
        '  printf "{\"status\":\"ok\",\"checks\":{\"voice_realtime\":{\"contract_version\":%s,\"providers\":[]}}}" "${FAKE_CONTRACT_VERSION:-2}"',
        'fi'
    )
    [IO.File]::WriteAllText((Join-Path $Directory "docker"), (($docker -join "`n") + "`n"))
    [IO.File]::WriteAllText((Join-Path $Directory "curl"), (($curl -join "`n") + "`n"))
}
