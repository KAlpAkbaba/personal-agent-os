<#
.SYNOPSIS
    Safe invocation of native command-line tools from Windows PowerShell 5.1.

.DESCRIPTION
    Windows PowerShell 5.1 does not reliably escape arguments it passes to native
    executables. An argument that itself contains double quotes is emitted with those quotes
    unescaped, so the child's CommandLineToArgvW splits one argument into several. Measured
    on this machine, the Device Service installer's own arguments:

        intended : binPath=   |   "C:\Program Files\PagentOS\agent\service\Pagent...exe" run
        emitted  : binPath= ""C:\Program Files\PagentOS\agent\service\Pagent...exe" run"
        received : binPath=   |   C:\Program   |   Files\PagentOS\...\Pagent...exe run

    sc.exe then found an unexpected token where it wanted `obj=` and returned 1639,
    ERROR_INVALID_COMMAND_LINE — which is why the install failed only at service
    registration, after publishing and ACL hardening had already succeeded. The failure
    surfaces only when elevated, because a non-elevated sc.exe fails at OpenSCManager (5)
    before it validates arguments at all.

    So this module never lets PowerShell serialize native arguments. It builds the command
    line itself using the documented CommandLineToArgvW rules, hands that exact string to
    ProcessStartInfo.Arguments, and captures stdout, stderr and the exit code.

    It also resolves system tools by absolute path under %SystemRoot%\System32. `sc` alone
    is an alias for Set-Content in PowerShell, and PATH is not trustworthy in a spawned
    installer shell.
#>

Set-StrictMode -Version Latest

function Get-SystemTool {
    <#
    .SYNOPSIS
        Absolute path to a System32 tool, or a clear error naming what is missing.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Name)

    $path = Join-Path (Join-Path $env:SystemRoot "System32") $Name
    if (-not (Test-Path -LiteralPath $path)) {
        throw "required system tool not found: $path"
    }

    return $path
}

function ConvertTo-NativeArgument {
    <#
    .SYNOPSIS
        Quote one argument so CommandLineToArgvW recovers it exactly.

    .DESCRIPTION
        The rules (from the Microsoft C runtime / CommandLineToArgvW contract):
        backslashes are literal except before a double quote; a run of N backslashes
        followed by a quote becomes 2N+1 backslashes plus the quote; a run of N backslashes
        at the end of a quoted argument becomes 2N. An argument with no whitespace and no
        quote needs no wrapping at all, which keeps `binPath=` and friends looking exactly as
        sc.exe documents them.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Argument)

    if ($Argument.Length -gt 0 -and $Argument -notmatch '[\s"]') {
        return $Argument
    }

    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')

    for ($i = 0; $i -lt $Argument.Length; $i++) {
        $backslashes = 0
        while ($i -lt $Argument.Length -and $Argument[$i] -eq '\') {
            $backslashes++
            $i++
        }

        if ($i -eq $Argument.Length) {
            # Trailing backslashes are doubled so the closing quote stays a delimiter.
            [void]$builder.Append('\' * ($backslashes * 2))
            break
        }

        if ($Argument[$i] -eq '"') {
            [void]$builder.Append('\' * ($backslashes * 2 + 1))
            [void]$builder.Append('"')
        }
        else {
            [void]$builder.Append('\' * $backslashes)
            [void]$builder.Append($Argument[$i])
        }
    }

    [void]$builder.Append('"')
    return $builder.ToString()
}

function ConvertTo-NativeArgumentLine {
    <#
    .SYNOPSIS
        Join arguments into the exact command line a native tool will parse back.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Arguments)

    return (($Arguments | ForEach-Object { ConvertTo-NativeArgument -Argument $_ }) -join ' ')
}

function Invoke-NativeProcess {
    <#
    .SYNOPSIS
        Run a native tool with an exactly-controlled command line; capture everything.

    .DESCRIPTION
        Returns an object with FilePath, CommandLine, ExitCode, StdOut, StdErr and Success.
        Never throws on a non-zero exit code — the caller decides what is acceptable, because
        several tools here have meaningful non-zero codes (sc.exe returns 1060 for "no such
        service", 1073 for "already exists").

        stdout and stderr are read concurrently. Reading one to the end before starting the
        other deadlocks as soon as the tool fills the other pipe's buffer, which for a
        chatty tool like icacls is not hypothetical.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Arguments,
        [int[]]$SuccessExitCodes = @(0),
        [int]$TimeoutSeconds = 120,
        [string]$WorkingDirectory
    )

    $commandLine = ConvertTo-NativeArgumentLine -Arguments $Arguments

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = $commandLine
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    # Push-Location changes PowerShell's location, NOT the process's working directory, so a
    # child launched here inherits wherever the shell was started — which is how
    # `uv run alembic` came back "program not found" while the same command worked in the
    # gate. Tools that resolve their environment from the current directory must be told
    # explicitly.
    if ($WorkingDirectory) {
        if (-not (Test-Path -LiteralPath $WorkingDirectory)) {
            throw "working directory does not exist: $WorkingDirectory"
        }
        $psi.WorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).Path
    }
    else {
        # Default to PowerShell's own location rather than the process's inherited one, so
        # `Push-Location X; Invoke-NativeProcess ...` behaves the way it reads.
        $psi.WorkingDirectory = (Get-Location -PSProvider FileSystem).ProviderPath
    }

    $process = [System.Diagnostics.Process]::Start($psi)
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()

    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $process.Kill() } catch { }
        throw "native tool timed out after $TimeoutSeconds s: $FilePath $commandLine"
    }

    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    $exitCode = $process.ExitCode
    $process.Dispose()

    return [pscustomobject]@{
        FilePath    = $FilePath
        CommandLine = $commandLine
        ExitCode    = $exitCode
        StdOut      = $stdout
        StdErr      = $stderr
        Success     = ($SuccessExitCodes -contains $exitCode)
    }
}

function Assert-NativeSuccess {
    <#
    .SYNOPSIS
        Turn a failed native invocation into an error that says what actually happened.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][psobject]$Result,
        [Parameter(Mandatory = $true)][string]$Activity
    )

    if ($Result.Success) {
        return
    }

    $detail = @(
        "$Activity failed with exit code $($Result.ExitCode)$(Get-NativeExitCodeHint -ExitCode $Result.ExitCode)",
        "  command: $($Result.FilePath) $($Result.CommandLine)"
    )
    if ($Result.StdOut -and $Result.StdOut.Trim()) {
        $detail += "  stdout : $($Result.StdOut.Trim() -replace "`r?`n", "`n           ")"
    }
    if ($Result.StdErr -and $Result.StdErr.Trim()) {
        $detail += "  stderr : $($Result.StdErr.Trim() -replace "`r?`n", "`n           ")"
    }

    throw ($detail -join [Environment]::NewLine)
}

function Get-NativeExitCodeHint {
    <#
    .SYNOPSIS
        Plain-language meaning for the exit codes this installer can actually hit.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][int]$ExitCode)

    switch ($ExitCode) {
        5    { " (ERROR_ACCESS_DENIED - run this from an elevated PowerShell)" }
        1056 { " (ERROR_SERVICE_ALREADY_RUNNING)" }
        1060 { " (ERROR_SERVICE_DOES_NOT_EXIST)" }
        1072 { " (ERROR_SERVICE_MARKED_FOR_DELETE - close services.msc and any open handle, then retry)" }
        1073 { " (ERROR_SERVICE_EXISTS)" }
        1639 { " (ERROR_INVALID_COMMAND_LINE - the arguments reached sc.exe malformed; this is a bug in the installer, not in your machine)" }
        default { "" }
    }
}

# ------------------------------------------------------------------ sc.exe argument shapes

function New-ScBinaryPathValue {
    <#
    .SYNOPSIS
        The value for sc.exe's binPath=, as a single argument.

    .DESCRIPTION
        When the service takes arguments, the executable must be quoted inside the value so
        the Service Control Manager does not treat "C:\Program" as the image and "Files\..."
        as an argument — the same class of bug as the one that produced 1639, but at service
        start rather than at registration. With no arguments the bare path is used and the
        quoting layer below wraps it as needed.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ExecutablePath,
        [string[]]$ServiceArguments = @()
    )

    if (-not $ServiceArguments -or @($ServiceArguments).Count -eq 0) {
        return $ExecutablePath
    }

    return ('"{0}" {1}' -f $ExecutablePath, ($ServiceArguments -join ' '))
}

function New-ScCreateArgumentList {
    <#
    .SYNOPSIS
        Exact argv for `sc.exe create`.

    .DESCRIPTION
        sc.exe wants each option as two tokens: the key WITH its trailing '=' and then the
        value. That is why every entry below is a separate array element and why nothing here
        interpolates values into a single string.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ServiceName,
        [Parameter(Mandatory = $true)][string]$BinaryPathValue,
        [string]$Account = "LocalSystem",
        [ValidateSet("auto", "delayed-auto", "demand", "disabled")][string]$StartMode = "auto",
        [string]$DisplayName
    )

    $arguments = @("create", $ServiceName, "binPath=", $BinaryPathValue, "obj=", $Account, "start=", $StartMode)
    if ($DisplayName) {
        $arguments += @("DisplayName=", $DisplayName)
    }

    return $arguments
}

function New-ScConfigArgumentList {
    <#
    .SYNOPSIS
        Exact argv for `sc.exe config` — the same shape, used to repoint an existing service.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ServiceName,
        [Parameter(Mandatory = $true)][string]$BinaryPathValue,
        [string]$Account = "LocalSystem",
        [ValidateSet("auto", "delayed-auto", "demand", "disabled")][string]$StartMode = "auto",
        [string]$DisplayName
    )

    $arguments = @("config", $ServiceName, "binPath=", $BinaryPathValue, "obj=", $Account, "start=", $StartMode)
    if ($DisplayName) {
        $arguments += @("DisplayName=", $DisplayName)
    }

    return $arguments
}

function New-ScFailureArgumentList {
    <#
    .SYNOPSIS
        Exact argv for `sc.exe failure`: restart rather than leave the desktop unreachable.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ServiceName,
        [int]$ResetSeconds = 86400,
        [string]$Actions = "restart/5000/restart/15000/restart/60000"
    )

    return @("failure", $ServiceName, "reset=", "$ResetSeconds", "actions=", $Actions)
}
