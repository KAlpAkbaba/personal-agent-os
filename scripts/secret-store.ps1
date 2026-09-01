<#
.SYNOPSIS
    Local secure secret entry for provider API keys: typed into a prompt, encrypted to the
    owner's Windows account, never written in plaintext and never pasted into a chat.

.DESCRIPTION
    The constitution says the owner must never be asked to paste secret values into chat
    history, and that the repository must provide a local secure secret-entry mechanism.
    This is it.

    Values are read with a masked prompt (never as a parameter, so they cannot land in
    PowerShell history or a process command line), encrypted with DPAPI under the owner's
    account, and written one-per-file under %LOCALAPPDATA%\PagentOS\secrets with an ACL that
    grants the owner alone. DPAPI means the ciphertext is useless to any other account on the
    machine and useless on any other machine, so a stolen copy of the file is not a stolen
    key.

    Nothing here ever prints a secret. `-Run` decrypts straight into the environment of a
    child process, so the plaintext exists only in that process's memory for as long as it
    runs — there is no decrypted file at any point.

.EXAMPLE
    # Store a key (prompts; nothing echoes, nothing is logged):
    .\scripts\secret-store.ps1 -Set PAGENTOS_VOICE_ELEVENLABS_API_KEY

.EXAMPLE
    # See what is stored, without values:
    .\scripts\secret-store.ps1 -List

.EXAMPLE
    # Run something with the secrets injected into its environment and nowhere else:
    .\scripts\secret-store.ps1 -Run "uv run uvicorn app.main:app" -WorkingDirectory services\api
#>
[CmdletBinding(DefaultParameterSetName = "List")]
param(
    [Parameter(ParameterSetName = "Set", Mandatory = $true)]
    [string]$Set,

    [Parameter(ParameterSetName = "Remove", Mandatory = $true)]
    [string]$Remove,

    [Parameter(ParameterSetName = "List")]
    [switch]$List,

    [Parameter(ParameterSetName = "Run", Mandatory = $true)]
    [string]$Run,

    [Parameter(ParameterSetName = "Run")]
    [string]$WorkingDirectory = ".",

    [string]$StoreRoot = (Join-Path $env:LOCALAPPDATA "PagentOS\secrets")
)

$ErrorActionPreference = "Stop"

function Initialize-Store {
    if (-not (Test-Path $StoreRoot)) {
        New-Item -ItemType Directory -Force -Path $StoreRoot | Out-Null
    }
    # Owner-only, inheritance stripped: the same posture as the identity root, for the same
    # reason. DPAPI already makes the bytes useless to others; the ACL means they do not get
    # to hold the bytes.
    $sid = ([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
    $grant = "*" + $sid + ":(OI)(CI)F"
    & icacls $StoreRoot /inheritance:r /grant:r $grant /Q 2>$null | Out-Null
}

function Get-SecretPath {
    param([string]$Name)
    if ($Name -notmatch '^[A-Za-z_][A-Za-z0-9_]{0,127}$') {
        throw "secret names must look like environment variables (letters, digits, underscore): '$Name'"
    }
    return Join-Path $StoreRoot "$Name.dpapi"
}

switch ($PSCmdlet.ParameterSetName) {
    "Set" {
        Initialize-Store
        $path = Get-SecretPath -Name $Set

        # Read-Host -AsSecureString: nothing echoes to the console, and the value never
        # appears as a parameter, so it stays out of history and out of any process listing.
        $secure = Read-Host -Prompt "Value for $Set (input is hidden)" -AsSecureString
        if ($secure.Length -eq 0) { throw "empty value; nothing stored" }

        # ConvertFrom-SecureString without a key uses DPAPI under the current user.
        $secure | ConvertFrom-SecureString | Set-Content -Path $path -Encoding ASCII
        Write-Host "stored $Set ($([Math]::Round((Get-Item $path).Length / 1KB, 1)) KB encrypted at $path)"
        Write-Host "It is readable only by this Windows account, on this machine."
    }

    "Remove" {
        $path = Get-SecretPath -Name $Remove
        if (Test-Path $path) {
            Remove-Item -Path $path -Force
            Write-Host "removed $Remove"
        }
        else {
            Write-Host "no stored secret named $Remove"
        }
    }

    "List" {
        if (-not (Test-Path $StoreRoot)) {
            Write-Host "no secrets stored yet (store root: $StoreRoot)"
            break
        }
        $items = Get-ChildItem -Path $StoreRoot -Filter "*.dpapi" -ErrorAction SilentlyContinue
        if (-not $items) {
            Write-Host "no secrets stored yet (store root: $StoreRoot)"
            break
        }
        Write-Host "stored secrets (names and timestamps only - values are never printed):"
        $items | ForEach-Object {
            [pscustomobject]@{
                Name    = [IO.Path]::GetFileNameWithoutExtension($_.Name)
                Updated = $_.LastWriteTime
            }
        } | Format-Table -AutoSize
    }

    "Run" {
        if (-not (Test-Path $StoreRoot)) { throw "no secrets stored; run -Set first" }

        $env_backup = @{}
        $loaded = @()
        try {
            foreach ($file in Get-ChildItem -Path $StoreRoot -Filter "*.dpapi") {
                $name = [IO.Path]::GetFileNameWithoutExtension($file.Name)
                $secure = Get-Content -Path $file.FullName | ConvertTo-SecureString
                $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
                try {
                    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
                    $env_backup[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
                    [Environment]::SetEnvironmentVariable($name, $plain, "Process")
                    $loaded += $name
                }
                finally {
                    # Zero the unmanaged copy immediately; the managed string is unavoidable
                    # but short-lived and never written anywhere.
                    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
                }
            }

            Write-Host "running with $(@($loaded).Count) secret(s) in the process environment: $($loaded -join ', ')"
            Push-Location $WorkingDirectory
            try {
                Invoke-Expression $Run
                $exit = $LASTEXITCODE
            }
            finally {
                Pop-Location
            }
            exit $exit
        }
        finally {
            # Put the environment back, so a secret does not outlive the command it was for.
            foreach ($name in $loaded) {
                [Environment]::SetEnvironmentVariable($name, $env_backup[$name], "Process")
            }
        }
    }
}
