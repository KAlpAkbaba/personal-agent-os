<#
.SYNOPSIS
    Transactional replacement of a JSON configuration file, with a real backup, on
    Windows PowerShell 5.1 / .NET Framework.

.DESCRIPTION
    Built after a real broker switch died inside `[System.IO.File]::Replace($tmp, $cfg, $null)`
    with "The path is not in a valid format". Probing the call proved the cause: the
    overload is Replace(String, String, String), the source and destination were absolute
    and valid, and the third argument is where it failed - PowerShell binds `$null` to a
    [string] parameter as an EMPTY STRING, and File.Replace rejects "" as a path. A real
    backup path on the same volume succeeds, and gives the rollback copy as part of the
    same atomic NTFS operation. So this module never passes null or empty for the backup.

    What this guarantees, and tests prove:

      * the staging file and the backup live in the SAME DIRECTORY as the destination, so
        all three are on one NTFS volume - File.Replace requires it, and it is what makes
        the replace atomic rather than a copy;
      * every path is normalised with [IO.Path]::GetFullPath and required to be rooted;
        nothing depends on the current working directory, and directories with spaces
        (Program Files) are ordinary;
      * a stale staging file from an interrupted earlier attempt is simply overwritten;
      * an existing backup is overwritten (File.Replace does that itself);
      * the replaced file is validated BEFORE it goes live (JSON, required keys) and read
        back AFTER, byte-for-byte, so "replaced" means "the bytes are there";
      * the file's DACL is checked after the replace: File.Replace keeps the destination's
        security descriptor, but that is verified, not assumed - an empty DACL on a config
        file has already taken a service down once in this project;
      * applying the same content twice is a no-op that reports Replaced = $false.

    No service control lives here: this is the file primitive, driven by
    scripts/switch-agent-broker.ps1 which owns stop/start/verify/rollback.
#>

Set-StrictMode -Version Latest

function Resolve-RootedPath {
    <#  Full, rooted path or a clear refusal. Never touches the current directory's meaning.  #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Purpose)

    if ([string]::IsNullOrWhiteSpace($Path)) { throw "$Purpose path is empty" }
    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        throw "$Purpose path must be absolute, got '$Path' - a relative path would depend on the current directory"
    }
    return [System.IO.Path]::GetFullPath($Path)
}

function Assert-SameDirectory {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Reference, [Parameter(Mandatory = $true)][string]$Candidate, [string]$Purpose = "path")

    $referenceDirectory = [System.IO.Path]::GetDirectoryName($Reference)
    $candidateDirectory = [System.IO.Path]::GetDirectoryName($Candidate)
    if (-not [string]::Equals($referenceDirectory, $candidateDirectory, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Purpose must live in the destination's own directory ($referenceDirectory) so the replace stays on one volume and atomic; got $candidateDirectory"
    }
}

function Test-ConfigFileAcl {
    <#
    .SYNOPSIS
        Is this file's DACL sane: non-empty, and readable by SYSTEM? Returns a report.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $acl = (Get-Item -LiteralPath $Path).GetAccessControl([System.Security.AccessControl.AccessControlSections]::Access)
    $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
    $systemRead = @($rules | Where-Object {
            $_.IdentityReference -eq $systemSid -and
            $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
            (($_.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::ReadData) -ne 0)
        })
    return [pscustomobject]@{
        Path       = $Path
        RuleCount  = @($rules).Count
        EmptyDacl  = (@($rules).Count -eq 0)
        SystemRead = (@($systemRead).Count -gt 0)
        Ok         = (@($rules).Count -gt 0 -and @($systemRead).Count -gt 0)
    }
}

function Invoke-AtomicConfigReplace {
    <#
    .SYNOPSIS
        Stage new content next to the destination, validate it, then replace atomically
        with a real backup. Returns what happened; throws before anything is live.

    .PARAMETER Validate
        Optional scriptblock given the STAGED path. Throw to refuse; the live file is never
        touched if validation fails.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$NewContent,
        [Parameter(Mandatory = $true)][string]$BackupPath,
        [scriptblock]$Validate
    )

    $destination = Resolve-RootedPath -Path $ConfigPath -Purpose "config"
    $backup = Resolve-RootedPath -Path $BackupPath -Purpose "backup"
    if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) { throw "config file does not exist: $destination" }
    Assert-SameDirectory -Reference $destination -Candidate $backup -Purpose "the backup"
    if ([string]::Equals($backup, $destination, [StringComparison]::OrdinalIgnoreCase)) { throw "backup path must differ from the config path" }

    $staged = "$destination.tmp"
    Assert-SameDirectory -Reference $destination -Candidate $staged -Purpose "the staging file"

    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $current = [System.IO.File]::ReadAllText($destination)
    if ([string]::Equals($current, $NewContent, [StringComparison]::Ordinal)) {
        # Idempotent: the bytes are already live. A stale staging file from an interrupted
        # run is cleaned up so it cannot confuse a later reader.
        Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue
        return [pscustomobject]@{ Replaced = $false; ConfigPath = $destination; BackupPath = $backup; StagedPath = $staged }
    }

    # Stage. WriteAllText overwrites, so an interrupted earlier attempt's .tmp is replaced
    # rather than trusted.
    [System.IO.File]::WriteAllText($staged, $NewContent, $utf8)

    try {
        # Structural validation always; caller's validation on top.
        $null = [System.IO.File]::ReadAllText($staged) | ConvertFrom-Json
        if ($null -ne $Validate) { & $Validate $staged }
    }
    catch {
        Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue
        throw "staged configuration rejected before going live: $($_.Exception.Message)"
    }

    # The atomic step. THREE real paths: the backup is never $null or "" - PowerShell binds
    # $null to a [string] parameter as an empty string, which File.Replace refuses with
    # "The path is not in a valid format". A real backup on the same volume is both the
    # fix and the rollback copy, produced by the same NTFS transaction.
    [System.IO.File]::Replace($staged, $destination, $backup)

    # Prove it, do not assume it.
    $live = [System.IO.File]::ReadAllText($destination)
    if (-not [string]::Equals($live, $NewContent, [StringComparison]::Ordinal)) {
        throw "the replace completed but the live file does not contain the staged bytes; roll back from $backup"
    }
    if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) {
        throw "the replace completed but no backup exists at $backup"
    }
    $aclReport = Test-ConfigFileAcl -Path $destination
    if (-not $aclReport.Ok) {
        throw "the replaced file's DACL is not usable (rules=$($aclReport.RuleCount) systemRead=$($aclReport.SystemRead)); the service could not read it. Roll back from $backup"
    }

    return [pscustomobject]@{ Replaced = $true; ConfigPath = $destination; BackupPath = $backup; StagedPath = $staged }
}

function Restore-ConfigFromBackup {
    <#  Put the backup's bytes back, atomically, keeping the failed content aside for diagnosis.  #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [Parameter(Mandatory = $true)][string]$BackupPath
    )

    $destination = Resolve-RootedPath -Path $ConfigPath -Purpose "config"
    $backup = Resolve-RootedPath -Path $BackupPath -Purpose "backup"
    if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) { throw "nothing to restore: $backup does not exist" }
    Assert-SameDirectory -Reference $destination -Candidate $backup -Purpose "the backup"

    $content = [System.IO.File]::ReadAllText($backup)
    $failedAside = "$destination.failed"
    return Invoke-AtomicConfigReplace -ConfigPath $destination -NewContent $content -BackupPath $failedAside
}

function New-AgentBrokerConfigContent {
    <#
    .SYNOPSIS
        The agent's appsettings.json with only the broker endpoints changed. Every other
        key (DataDir, PipeName, CompanionSid, CompanionImagePath) is preserved verbatim -
        those are the qualified runtime's identity and must not drift during a migration.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$CurrentContent,
        [Parameter(Mandatory = $true)][string]$BrokerHost,
        [Parameter(Mandatory = $true)][int]$Port
    )

    $config = $CurrentContent | ConvertFrom-Json
    foreach ($required in @("DataDir", "PipeName", "CompanionSid", "CompanionImagePath")) {
        if (-not (Test-ObjectProperty -InputObject $config -Name $required)) {
            throw "the current configuration has no '$required' - refusing to rewrite a file that is not the installed agent's"
        }
    }
    $config.BrokerRestUrl = "http://${BrokerHost}:$Port"
    $config.BrokerWsUrl = "ws://${BrokerHost}:$Port/v1/devices/connect"
    return ($config | ConvertTo-Json)
}

function Test-AgentBrokerConfig {
    <#  Validation for a staged agent config: parses, keeps the runtime identity, endpoints well-formed.  #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$StagedPath, [Parameter(Mandatory = $true)][string]$ExpectedRestUrl)

    $config = [System.IO.File]::ReadAllText($StagedPath) | ConvertFrom-Json
    foreach ($required in @("BrokerRestUrl", "BrokerWsUrl", "DataDir", "PipeName", "CompanionSid", "CompanionImagePath")) {
        if (-not (Test-ObjectProperty -InputObject $config -Name $required)) { throw "staged config lacks '$required'" }
    }
    if ($config.BrokerRestUrl -ne $ExpectedRestUrl) { throw "staged BrokerRestUrl is '$($config.BrokerRestUrl)', expected '$ExpectedRestUrl'" }
    $rest = $null; $ws = $null
    if (-not [System.Uri]::TryCreate($config.BrokerRestUrl, [System.UriKind]::Absolute, [ref]$rest) -or $rest.Scheme -notin @("http", "https")) {
        throw "BrokerRestUrl is not an absolute http(s) URL"
    }
    if (-not [System.Uri]::TryCreate($config.BrokerWsUrl, [System.UriKind]::Absolute, [ref]$ws) -or $ws.Scheme -notin @("ws", "wss")) {
        throw "BrokerWsUrl is not an absolute ws(s) URL"
    }
    if ($rest.Host -ne $ws.Host) { throw "BrokerRestUrl and BrokerWsUrl point at different hosts" }
}
