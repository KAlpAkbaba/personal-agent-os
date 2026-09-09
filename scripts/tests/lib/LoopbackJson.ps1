<#
.SYNOPSIS
    A loopback JSON stub, and the two ways of starting the installer's Cloud Core verifier
    in a clean child process. Shared by scripts\tests\core-verifier-scope.tests.ps1 and by
    scripts\qualify-staged-update.ps1, so the regression and the qualification cannot drift.

.DESCRIPTION
    A raw TcpListener, not HttpListener: HttpListener needs a URL ACL or elevation, and a
    check that has to be run as administrator is a check that stops being run.

    Invoke-CoreVerifierChild starts scripts\tests\lib\CoreVerifierChild.ps1 - which loads the
    installer's libraries and builds the REAL fetcher - in one of two modes:

        command   through a one-line wrapper that INVOKES it, so it gets a child script
                  scope: what happens when the owner types `.\scripts\install-...ps1`
        file      powershell -File, which makes it the top-level scope: what every harness
                  in this repository has always done

    Those two modes gave different answers on 2026-09-09 and that difference is the defect
    this file exists to keep measuring. Both are always run; a fix that only works in one of
    them is not a fix.

    Dot-source. Windows PowerShell 5.1, StrictMode-safe. Needs Invoke-NativeProcess
    (scripts\lib\NativeProcess.ps1) from whatever dot-sourced it.
#>

Set-StrictMode -Version Latest

function Start-JsonStub {
    <#
    .SYNOPSIS
        Serve one JSON document, as UTF-8 with a declared charset, on 127.0.0.1:<free port>,
        for as long as $Seconds. Returns the port and the handles Stop-JsonStub needs.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Json, [int]$Seconds = 60)
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $worker = [powershell]::Create()
    [void]$worker.AddScript({
            param($listener, $json, $deadline)
            $bytes = [Text.Encoding]::UTF8.GetBytes($json)
            while ([DateTime]::UtcNow -lt $deadline) {
                if (-not $listener.Pending()) { Start-Sleep -Milliseconds 20; continue }
                $client = $listener.AcceptTcpClient()
                try {
                    $stream = $client.GetStream()
                    $buffer = New-Object byte[] 4096
                    # A GET has no body, so one bounded read is enough to be sure the client
                    # finished writing its request head before we answer.
                    [void]$stream.Read($buffer, 0, $buffer.Length)
                    $head = [Text.Encoding]::ASCII.GetBytes(
                        "HTTP/1.1 200 OK`r`nContent-Type: application/json; charset=utf-8`r`nContent-Length: $($bytes.Length)`r`nConnection: close`r`n`r`n")
                    $stream.Write($head, 0, $head.Length)
                    $stream.Write($bytes, 0, $bytes.Length)
                    $stream.Flush()
                }
                catch { }
                finally { $client.Close() }
            }
        }).AddArgument($listener).AddArgument($Json).AddArgument([DateTime]::UtcNow.AddSeconds($Seconds))
    $null = $worker.BeginInvoke()
    return [pscustomobject]@{
        Port     = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
        Listener = $listener
        Worker   = $worker
    }
}

function Stop-JsonStub {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Stub)
    try { $Stub.Listener.Stop() } catch { }
    try { [void]$Stub.Worker.Stop() } catch { }
    try { $Stub.Worker.Dispose() } catch { }
}

function New-CoreDeviceListingJson {
    <#  One device row, in the shape services/api/app/devices/types.py emits.  #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$DeviceId,
        [Parameter(Mandatory = $true)][string]$Version,
        [Parameter(Mandatory = $true)][string[]]$Capabilities,
        [string]$Presence = "online"
    )
    #: Non-ASCII in the name, written as escapes so every file that builds this stays ASCII
    #: (5.1 reads a BOM-less script as ANSI). Invoke-JsonUtf8 exists because 5.1 decodes a
    #: charset-less body as ISO-8859-1, and its decode helpers - Read-AllBytes,
    #: ConvertFrom-Utf8Json - live in the same script scope that the closure lost. A fix that
    #: resolved only the OUTER name would reach the network and fail here instead, against a
    #: real Cloud Core and nowhere else; a body that must be decoded to parse closes that.
    $name = "MAIL - " + [string][char]0x00F6 + "l" + [string][char]0x00E7 + "1 d" + [string][char]0x00FC + "g" + [string][char]0x00FC + "m"
    return ([ordered]@{
            devices = @(
                [ordered]@{
                    device_id        = $DeviceId
                    name             = $name
                    presence         = $Presence
                    software_version = $Version
                    capabilities     = @($Capabilities)
                    last_seen_at     = "2026-09-09T11:04:50.000000Z"
                }
            )
        } | ConvertTo-Json -Depth 6 -Compress)
}

function Invoke-CoreVerifierChild {
    <#
    .SYNOPSIS
        Run the installer's REAL Cloud Core verifier in a clean child process and return its
        one-line JSON verdict.
    .PARAMETER Mode
        "command" (the owner's invocation form) or "file" (every harness's).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][ValidateSet("command", "file")][string]$Mode,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$Sandbox,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$DeviceId,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion,
        [Parameter(Mandatory = $true)][string[]]$ExpectedCapabilities,
        [int]$TimeoutSeconds = 6,
        [int]$PollSeconds = 1
    )
    $child = Join-Path $RepoRoot "scripts\tests\lib\CoreVerifierChild.ps1"
    $shell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $childArgs = @(
        "-BaseUrl", "http://127.0.0.1:$Port",
        "-DeviceId", $DeviceId,
        "-ExpectedVersion", $ExpectedVersion,
        "-ExpectedCapabilities", ($ExpectedCapabilities -join ","),
        "-TimeoutSeconds", [string]$TimeoutSeconds,
        "-PollSeconds", [string]$PollSeconds
    )
    if ($Mode -eq "command") {
        New-Item -ItemType Directory -Force -Path $Sandbox | Out-Null
        $runner = Join-Path $Sandbox ("runner-" + [guid]::NewGuid().ToString("N") + ".ps1")
        $rendered = ($childArgs | ForEach-Object { if ($_.StartsWith("-")) { $_ } else { "'$_'" } }) -join " "
        [IO.File]::WriteAllText($runner, "& '$child' $rendered`r`n", [Text.UTF8Encoding]::new($false))
        $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $runner)
    }
    else {
        $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $child) + $childArgs
    }
    $run = Invoke-NativeProcess -FilePath $shell -Arguments $arguments -TimeoutSeconds 120
    $line = ($run.StdOut -split "`n" | Where-Object { $_.Trim().StartsWith("{") } | Select-Object -Last 1)
    if (-not $line) {
        throw "the Cloud Core verifier printed no verdict (mode $Mode, exit $($run.ExitCode)). stdout: $($run.StdOut) stderr: $($run.StdErr)"
    }
    return ($line | ConvertFrom-Json)
}
