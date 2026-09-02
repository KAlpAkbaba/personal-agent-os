<#
.SYNOPSIS
    Read access to the local DPAPI secret store that scripts/secret-store.ps1 writes.

.DESCRIPTION
    secret-store.ps1 -Set stores one value per file under %LOCALAPPDATA%\PagentOS\secrets as
    the output of ConvertFrom-SecureString (DPAPI, current user, this machine). This library
    is the ONLY other reader: it decrypts a named value into a short-lived managed string
    for a caller that must hand it to a child process or a remote stdin, and it never logs,
    prints or writes the value anywhere.

    Dot-source it; Windows PowerShell 5.1, StrictMode-safe.
#>

Set-StrictMode -Version Latest

function Get-SecretStoreRoot {
    [CmdletBinding()]
    param([string]$StoreRoot)
    if ($StoreRoot) { return $StoreRoot }
    return (Join-Path $env:LOCALAPPDATA "PagentOS\secrets")
}

function Test-SecretName {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Name)
    # Environment-variable shape only. Anything else could not be a valid .env key and
    # would be a way to smuggle shell syntax into a remote command.
    return [bool]($Name -cmatch '^[A-Za-z_][A-Za-z0-9_]{0,127}$')
}

function Get-SecretStorePath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$StoreRoot
    )
    if (-not (Test-SecretName -Name $Name)) {
        throw "secret names must look like environment variables (letters, digits, underscore): '$Name'"
    }
    return (Join-Path (Get-SecretStoreRoot -StoreRoot $StoreRoot) "$Name.dpapi")
}

function Get-StoredSecretValue {
    <#
    .SYNOPSIS
        Decrypt one stored secret. Returns the plaintext string; the caller owns it and
        must not print it. Throws (without revealing anything) when the secret is absent.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$StoreRoot
    )
    $path = Get-SecretStorePath -Name $Name -StoreRoot $StoreRoot
    if (-not (Test-Path -LiteralPath $path)) {
        throw ("no stored secret named $Name. Store it first with a masked prompt: " +
               ".\scripts\secret-store.ps1 -Set $Name")
    }
    $cipher = (Get-Content -LiteralPath $path -Raw).Trim()
    if (-not $cipher) { throw "stored secret $Name is empty; re-run secret-store.ps1 -Set $Name" }
    $secure = $cipher | ConvertTo-SecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function ConvertTo-NativeArgument {
    <#
    .SYNOPSIS
        Escape one argument for a native .exe under Windows PowerShell 5.1.

    .DESCRIPTION
        5.1 wraps an argument that contains whitespace in double quotes but does NOT escape
        the double quotes inside it, so a remote shell command such as  grep -v "^$name="
        reaches ssh.exe torn apart. The C runtime the receiving program uses wants \" for an
        embedded quote, and a backslash run that precedes a quote (or ends the argument)
        doubled. This is that rule, and cloud-secret.tests.ps1 proves it against a real
        native process rather than by reading about it.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    $quoteEvaluator = [System.Text.RegularExpressions.MatchEvaluator] { param($m) ($m.Groups[1].Value * 2) + '\"' }
    $escaped = [regex]::Replace($Value, '(\\*)"', $quoteEvaluator)
    $tailEvaluator = [System.Text.RegularExpressions.MatchEvaluator] { param($m) $m.Groups[1].Value * 2 }
    $escaped = [regex]::Replace($escaped, '(\\+)$', $tailEvaluator)
    return $escaped
}
