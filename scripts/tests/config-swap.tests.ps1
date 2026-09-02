<#
.SYNOPSIS
    Windows PowerShell 5.1 StrictMode tests for the transactional config replace
    (scripts/lib/ConfigSwap.ps1) - the primitive under the broker switch.

.DESCRIPTION
    The incident: a real broker switch died in `[IO.File]::Replace($tmp, $cfg, $null)` with
    "The path is not in a valid format". The probe that found the cause is repeated here as
    a test so the class stays understood: PowerShell binds $null to a [string] parameter as
    an EMPTY STRING, and File.Replace refuses "" as a path. Everything else here is the
    behaviour the owner required of the primitive - Program Files-style paths, absolute
    paths, an existing destination, an interrupted (stale) staging file, an existing
    backup, rollback, and idempotent repetition - all against real files in a directory
    whose name contains a space.

    Run: powershell -NoProfile -File scripts\tests\config-swap.tests.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\ConfigSwap.ps1")

$script:Failures = 0
$script:Passes = 0
# A space in the path on purpose: the real destination is under "Program Files".
$script:Sandbox = Join-Path $env:TEMP "pagentos config swap $([guid]::NewGuid().ToString('N'))"

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
    catch { $script:Failures++; Write-Host "  FAIL  $Name" -ForegroundColor Red; Write-Host "        $($_.Exception.Message)" -ForegroundColor Red }
}
function Assert-True { param([bool]$Condition, [string]$Because) if (-not $Condition) { throw $Because } }
function Assert-Equal { param($Expected, $Actual, [string]$Because) if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" } }

$installedShape = @'
{
    "BrokerRestUrl":  "http://127.0.0.1:8001",
    "BrokerWsUrl":  "ws://127.0.0.1:8001/v1/devices/connect",
    "DataDir":  "C:\\ProgramData\\PagentOS\\agent",
    "PipeName":  "pagentos-companion-S-1-5-21-1-2-3-1002",
    "CompanionSid":  "S-1-5-21-1-2-3-1002",
    "CompanionImagePath":  "C:\\Program Files\\PagentOS\\agent\\companion\\PagentOS.SessionCompanion.exe"
}
'@

function New-InstalledConfig {
    <#  A service directory shaped like the installed one, in a path with a space.  #>
    param([string]$Name)
    $dir = Join-Path $script:Sandbox "$Name\Program Files-like\PagentOS\agent\service"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $path = Join-Path $dir "appsettings.json"
    [System.IO.File]::WriteAllText($path, $installedShape, (New-Object System.Text.UTF8Encoding($false)))
    return $path
}

New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

try {
    Write-Host ""
    Write-Host "the engine under test"
    Test-Case "this really is Windows PowerShell 5.1 with StrictMode" {
        Assert-Equal -Expected 5 -Actual $PSVersionTable.PSVersion.Major -Because "5.1 compatibility tests"
        $threw = $false; try { $nothing = $null; $null = $nothing.Count } catch { $threw = $true }
        Assert-True -Condition $threw -Because "StrictMode is not in force"
    }

    Write-Host ""
    Write-Host "the incident: the third argument of File.Replace"
    Test-Case "PowerShell binds `$null to a [string] parameter as an EMPTY string, and File.Replace refuses it" {
        $config = New-InstalledConfig -Name "null-backup"
        $staged = "$config.tmp"
        [System.IO.File]::WriteAllText($staged, "{}", (New-Object System.Text.UTF8Encoding($false)))
        $threw = $false; $message = ""
        try { [System.IO.File]::Replace($staged, $config, $null) } catch { $threw = $true; $message = $_.Exception.InnerException.GetType().Name }
        Assert-True -Condition $threw -Because "the incident must still reproduce on this engine"
        Assert-Equal -Expected "ArgumentException" -Actual $message -Because "and it is an ArgumentException on the path, not an IO error"
        Assert-Equal -Expected $installedShape -Actual ([System.IO.File]::ReadAllText($config)) -Because "the failed call leaves the live file untouched"
    }

    Test-Case "the primitive never passes an empty backup: every path is absolute, rooted, and on one volume" {
        $config = New-InstalledConfig -Name "shapes"
        $backup = Join-Path (Split-Path -Parent $config) "appsettings.previous.json"
        $newContent = New-AgentBrokerConfigContent -CurrentContent ([System.IO.File]::ReadAllText($config)) -BrokerHost "100.90.158.26" -Port 8001
        $outcome = Invoke-AtomicConfigReplace -ConfigPath $config -NewContent $newContent -BackupPath $backup
        foreach ($p in @($outcome.ConfigPath, $outcome.BackupPath, $outcome.StagedPath)) {
            Assert-True -Condition ([System.IO.Path]::IsPathRooted($p)) -Because "$p must be rooted"
            Assert-Equal -Expected ([System.IO.Path]::GetDirectoryName($outcome.ConfigPath)) -Actual ([System.IO.Path]::GetDirectoryName($p)) -Because "$p must be in the destination's directory (same volume)"
            Assert-True -Condition ($p -match " ") -Because "the path with a space must survive intact: $p"
        }
        Assert-True -Condition $outcome.Replaced -Because "the content changed, so it must report Replaced"
    }

    Test-Case "a relative config path is refused before anything is touched" {
        $threw = $false
        try { [void](Invoke-AtomicConfigReplace -ConfigPath "relative\appsettings.json" -NewContent "{}" -BackupPath "relative\b.json") }
        catch { $threw = ($_.Exception.Message -match "absolute") }
        Assert-True -Condition $threw -Because "relative paths would depend on the current directory"
    }

    Test-Case "a backup on a different directory is refused (it would not be one atomic volume operation)" {
        $config = New-InstalledConfig -Name "other-dir"
        $threw = $false
        try { [void](Invoke-AtomicConfigReplace -ConfigPath $config -NewContent "{}" -BackupPath (Join-Path $env:TEMP "elsewhere.json")) }
        catch { $threw = ($_.Exception.Message -match "own directory") }
        Assert-True -Condition $threw -Because "the backup must live beside the destination"
    }

    Write-Host ""
    Write-Host "the transaction"
    Test-Case "the replaced file has the staged bytes, a backup with the old bytes, and only the broker keys changed" {
        $config = New-InstalledConfig -Name "replace"
        $backup = Join-Path (Split-Path -Parent $config) "appsettings.previous.json"
        $before = [System.IO.File]::ReadAllText($config)
        $newContent = New-AgentBrokerConfigContent -CurrentContent $before -BrokerHost "100.90.158.26" -Port 8001
        $validator = { param($stagedPath) Test-AgentBrokerConfig -StagedPath $stagedPath -ExpectedRestUrl "http://100.90.158.26:8001" }
        $outcome = Invoke-AtomicConfigReplace -ConfigPath $config -NewContent $newContent -BackupPath $backup -Validate $validator
        Assert-True -Condition $outcome.Replaced -Because "must replace"
        Assert-Equal -Expected $newContent -Actual ([System.IO.File]::ReadAllText($config)) -Because "live bytes == staged bytes"
        Assert-Equal -Expected $before -Actual ([System.IO.File]::ReadAllText($backup)) -Because "backup bytes == previous bytes"
        Assert-True -Condition (-not (Test-Path -LiteralPath $outcome.StagedPath)) -Because "File.Replace consumes the staging file"
        $live = [System.IO.File]::ReadAllText($config) | ConvertFrom-Json
        Assert-Equal -Expected "http://100.90.158.26:8001" -Actual $live.BrokerRestUrl -Because "rest endpoint switched"
        Assert-Equal -Expected "ws://100.90.158.26:8001/v1/devices/connect" -Actual $live.BrokerWsUrl -Because "ws endpoint switched"
        Assert-Equal -Expected "S-1-5-21-1-2-3-1002" -Actual $live.CompanionSid -Because "the runtime identity is preserved verbatim"
        Assert-Equal -Expected "C:\ProgramData\PagentOS\agent" -Actual $live.DataDir -Because "DataDir preserved"
    }

    Test-Case "a stale staging file from an interrupted attempt is overwritten, not trusted" {
        $config = New-InstalledConfig -Name "interrupted"
        $backup = Join-Path (Split-Path -Parent $config) "appsettings.previous.json"
        [System.IO.File]::WriteAllText("$config.tmp", "{ this is the interrupted run's garbage", (New-Object System.Text.UTF8Encoding($false)))
        $newContent = New-AgentBrokerConfigContent -CurrentContent ([System.IO.File]::ReadAllText($config)) -BrokerHost "100.90.158.26" -Port 8001
        $outcome = Invoke-AtomicConfigReplace -ConfigPath $config -NewContent $newContent -BackupPath $backup
        Assert-True -Condition $outcome.Replaced -Because "must proceed despite the stale file"
        Assert-Equal -Expected $newContent -Actual ([System.IO.File]::ReadAllText($config)) -Because "the live file holds the NEW content, not the garbage"
    }

    Test-Case "an existing backup from an earlier switch is overwritten" {
        $config = New-InstalledConfig -Name "existing-backup"
        $backup = Join-Path (Split-Path -Parent $config) "appsettings.previous.json"
        [System.IO.File]::WriteAllText($backup, "an older backup", (New-Object System.Text.UTF8Encoding($false)))
        $before = [System.IO.File]::ReadAllText($config)
        $newContent = New-AgentBrokerConfigContent -CurrentContent $before -BrokerHost "100.90.158.26" -Port 8001
        [void](Invoke-AtomicConfigReplace -ConfigPath $config -NewContent $newContent -BackupPath $backup)
        Assert-Equal -Expected $before -Actual ([System.IO.File]::ReadAllText($backup)) -Because "the backup now holds the config that was live just before"
    }

    Test-Case "invalid staged content is rejected BEFORE going live" {
        $config = New-InstalledConfig -Name "invalid"
        $backup = Join-Path (Split-Path -Parent $config) "appsettings.previous.json"
        $before = [System.IO.File]::ReadAllText($config)
        $threw = $false
        try { [void](Invoke-AtomicConfigReplace -ConfigPath $config -NewContent "{ not json" -BackupPath $backup) }
        catch { $threw = ($_.Exception.Message -match "rejected before going live") }
        Assert-True -Condition $threw -Because "structural validation must refuse"
        Assert-Equal -Expected $before -Actual ([System.IO.File]::ReadAllText($config)) -Because "live file untouched"
        Assert-True -Condition (-not (Test-Path -LiteralPath $backup)) -Because "no backup is written for a refused replace"
        Assert-True -Condition (-not (Test-Path -LiteralPath "$config.tmp")) -Because "the rejected staging file is cleaned up"
    }

    Test-Case "the caller's validator can refuse: a config that lost its runtime identity never goes live" {
        $config = New-InstalledConfig -Name "validator"
        $backup = Join-Path (Split-Path -Parent $config) "appsettings.previous.json"
        $before = [System.IO.File]::ReadAllText($config)
        $stripped = '{"BrokerRestUrl":"http://100.90.158.26:8001","BrokerWsUrl":"ws://100.90.158.26:8001/v1/devices/connect"}'
        $validator = { param($stagedPath) Test-AgentBrokerConfig -StagedPath $stagedPath -ExpectedRestUrl "http://100.90.158.26:8001" }
        $threw = $false
        try { [void](Invoke-AtomicConfigReplace -ConfigPath $config -NewContent $stripped -BackupPath $backup -Validate $validator) }
        catch { $threw = ($_.Exception.Message -match "lacks 'DataDir'") }
        Assert-True -Condition $threw -Because "a config without DataDir/PipeName/CompanionSid must be refused"
        Assert-Equal -Expected $before -Actual ([System.IO.File]::ReadAllText($config)) -Because "live file untouched"
    }

    Test-Case "rollback restores the previous bytes atomically and keeps the failed config aside" {
        $config = New-InstalledConfig -Name "rollback"
        $backup = Join-Path (Split-Path -Parent $config) "appsettings.previous.json"
        $before = [System.IO.File]::ReadAllText($config)
        $newContent = New-AgentBrokerConfigContent -CurrentContent $before -BrokerHost "100.90.158.26" -Port 8001
        [void](Invoke-AtomicConfigReplace -ConfigPath $config -NewContent $newContent -BackupPath $backup)
        $restored = Restore-ConfigFromBackup -ConfigPath $config -BackupPath $backup
        Assert-Equal -Expected $before -Actual ([System.IO.File]::ReadAllText($config)) -Because "live file is the previous config again"
        Assert-Equal -Expected $newContent -Actual ([System.IO.File]::ReadAllText($restored.BackupPath)) -Because "the failed (cloud) config is kept aside for diagnosis"
        Assert-True -Condition ($restored.BackupPath -like "*.failed") -Because "under a name that says what it is"
    }

    Test-Case "repeating the migration is idempotent: second run replaces nothing and leaves the backup alone" {
        $config = New-InstalledConfig -Name "idempotent"
        $backup = Join-Path (Split-Path -Parent $config) "appsettings.previous.json"
        $before = [System.IO.File]::ReadAllText($config)
        $newContent = New-AgentBrokerConfigContent -CurrentContent $before -BrokerHost "100.90.158.26" -Port 8001
        $first = Invoke-AtomicConfigReplace -ConfigPath $config -NewContent $newContent -BackupPath $backup
        $backupBytes = [System.IO.File]::ReadAllText($backup)
        $second = Invoke-AtomicConfigReplace -ConfigPath $config -NewContent $newContent -BackupPath $backup
        Assert-True -Condition $first.Replaced -Because "first run replaces"
        Assert-True -Condition (-not $second.Replaced) -Because "second run finds the bytes already live"
        Assert-Equal -Expected $backupBytes -Actual ([System.IO.File]::ReadAllText($backup)) -Because "the original backup is not clobbered by a no-op run"
        Assert-Equal -Expected $newContent -Actual ([System.IO.File]::ReadAllText($config)) -Because "live content stable"
    }

    Test-Case "the replaced file keeps a usable DACL that SYSTEM can read" {
        $config = New-InstalledConfig -Name "acl"
        $backup = Join-Path (Split-Path -Parent $config) "appsettings.previous.json"
        $newContent = New-AgentBrokerConfigContent -CurrentContent ([System.IO.File]::ReadAllText($config)) -BrokerHost "100.90.158.26" -Port 8001
        [void](Invoke-AtomicConfigReplace -ConfigPath $config -NewContent $newContent -BackupPath $backup)
        $report = Test-ConfigFileAcl -Path $config
        Assert-True -Condition (-not $report.EmptyDacl) -Because "an empty DACL has taken this service down before"
        Assert-True -Condition $report.SystemRead -Because "the service account must be able to read what it loads (rules=$($report.RuleCount))"
    }
}
finally {
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "$($script:Passes) passed, $($script:Failures) failed"
exit $script:Failures
