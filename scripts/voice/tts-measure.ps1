<#
.SYNOPSIS
  FreyaTTS-small measurement (tts-freya-measure): MEASUREMENT ONLY. No provider is wired, no
  setting changes, nothing is added to services/api.

.DESCRIPTION
  1. Refuses to start unless this machine is on the allow-list (the home PC and the Cloud Core;
     never the employer's office PC) - before any docker call.
  2. Builds the image from tools/tts-measure (pinned base digest, pip --require-hashes, the code
     at the pinned commit with its file hashes) unless it already exists (-Rebuild forces a
     --no-cache build), then proves it with a `docker run --network none ... selfcheck`.
  3. Fills the named weight volume once: the three files by their pinned URLs, sha256-checked.
     A mismatch stops the run with a non-zero exit; nothing is synthesized, no evidence written.
  4. Runs the twenty OWNER_SENTENCES through `synthesize.py synth` with --network none,
     --read-only, --memory 8g with no swap (an overrun is an OOM failure, never a swapping
     slowdown that would distort RTF), the weights mounted read-only, -Cpus passed to docker --cpus and -Threads to
     torch. Every wait has a deadline; a container past it is killed and removed.
  5. Feeds the container's JSON lines to `python -m app.voice.tts_measure merge`, which merges
     this -Label into <EvidenceDir>/tts-freya-measure.json + .md (a re-run of a label replaces
     only that label).
  The WAVs go to %LOCALAPPDATA%\PagentOS\tts-measure\<label>\ (Linux: ~/.local/share/...),
  never inside the repository.

  Runs unchanged on Linux PowerShell 7 (the Cloud Core) - that run is a separate, remote step.

.EXAMPLE
  .\scripts\voice\tts-measure.ps1 -Label ev-pc
  .\scripts\voice\tts-measure.ps1 -Label cpx32-bicimi -Cpus 4 -Threads 4
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidatePattern("^[a-z0-9][a-z0-9-]{0,39}$")][string]$Label,
    [string]$Cpus = "",
    [int]$Threads = 0,
    [string]$EvidenceDir = "",
    [string]$Docker = "",
    [string]$Python = "",
    [string]$Image = "",
    [string]$WeightsVolume = "pagentos-freya-weights",
    [string]$MemoryLimit = "8g",
    [int]$TimeoutSec = 3600,
    [int]$BuildTimeoutSec = 3600,
    [int]$FillTimeoutSec = 1800,
    [double]$MinFreeGB = 20,
    [string[]]$AllowedHosts = @("MAIL", "pagentos-core"),
    [switch]$Rebuild
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$onWindows = [System.IO.Path]::DirectorySeparatorChar -eq '\'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$toolDir = Join-Path $repoRoot "tools/tts-measure"
$apiDir = Join-Path $repoRoot "services/api"
if (-not $EvidenceDir) { $EvidenceDir = Join-Path $repoRoot "docs/evidence" }
if ($Threads -le 0) { $Threads = [Environment]::ProcessorCount }
if (-not $Image) {
    # Content-addressed tag: a changed Dockerfile / lock / script is a new image, never a stale one.
    $parts = foreach ($name in @("Dockerfile", "requirements.txt", "fetch_code.py", "synthesize.py")) {
        (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $toolDir $name)).Hash
    }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $digest = -join ($sha.ComputeHash([System.Text.Encoding]::ASCII.GetBytes($parts -join "`n")) | ForEach-Object { $_.ToString("x2") })
    $Image = "pagentos-freya-measure:" + $digest.Substring(0, 12)
}

function Format-Argument([string]$Value) {
    if ($Value -eq "") { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    return '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

# Runs a native command with a deadline; stdin optional; never merges stderr into stdout.
function Invoke-Native {
    param([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSeconds, [byte[]]$StdIn = $null,
          [string]$WorkingDirectory = $repoRoot)
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    $info.Arguments = ($Arguments | ForEach-Object { Format-Argument $_ }) -join " "
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.RedirectStandardInput = $true
    $info.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $info.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $info.WorkingDirectory = $WorkingDirectory
    $process = [System.Diagnostics.Process]::Start($info)
    $outTask = $process.StandardOutput.ReadToEndAsync()
    $errTask = $process.StandardError.ReadToEndAsync()
    try {
        if ($null -ne $StdIn) { $process.StandardInput.BaseStream.Write($StdIn, 0, $StdIn.Length) }
        $process.StandardInput.Close()
    }
    catch [System.IO.IOException] { }  # the child stopped reading (e.g. a weight mismatch before stdin)
    $timedOut = -not $process.WaitForExit($TimeoutSeconds * 1000)
    if ($timedOut) {
        if ($onWindows) {
            $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
            $kill = New-Object System.Diagnostics.ProcessStartInfo
            $kill.FileName = $taskkill
            $kill.Arguments = "/T /F /PID $($process.Id)"
            $kill.UseShellExecute = $false
            $kill.CreateNoWindow = $true
            [void]([System.Diagnostics.Process]::Start($kill)).WaitForExit(60000)
        }
        else { $process.Kill() }
        [void]$process.WaitForExit(60000)
    }
    else { $process.WaitForExit() }
    [void]$outTask.Wait(60000); [void]$errTask.Wait(60000)
    $code = if ($timedOut) { -1 } else { $process.ExitCode }
    return [pscustomobject]@{
        ExitCode = $code; TimedOut = $timedOut
        StdOut = $(if ($outTask.IsCompleted) { $outTask.Result } else { "" })
        StdErr = $(if ($errTask.IsCompleted) { $errTask.Result } else { "" })
    }
}

function Stop-Measure([int]$Code, [string]$Message) {
    [Console]::Error.WriteLine("tts-measure: $Message")
    exit $Code
}

# 1. allow-list, before anything touches docker
$hostName = if ($env:COMPUTERNAME) { $env:COMPUTERNAME } else { [Environment]::MachineName }
if (@($AllowedHosts | Where-Object { $_ -ieq $hostName }).Count -eq 0) {
    Stop-Measure 4 "host '$hostName' is not on the allow-list ($($AllowedHosts -join ', ')); never run this on the office PC"
}

if (-not $Docker) {
    $Docker = if ($onWindows) { "C:\Program Files\Docker\Docker\resources\bin\docker.exe" } else { "/usr/bin/docker" }
}
if (-not (Test-Path -LiteralPath $Docker -PathType Leaf)) { Stop-Measure 2 "docker not found at $Docker" }

$pythonArgs = @()
if (-not $Python) {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -eq $uv) { Stop-Measure 2 "neither -Python nor uv is available" }
    $Python = $uv.Source
    $pythonArgs = @("run", "python")
}

if ($MinFreeGB -gt 0) {
    $drive = New-Object System.IO.DriveInfo ($(if ($onWindows) { "C" } else { "/" }))
    $freeGB = [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
    if ($freeGB -lt $MinFreeGB) { Stop-Measure 2 "only $freeGB GB free on $($drive.Name) (Docker's disk); need $MinFreeGB GB" }
}

$localBase = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME ".local/share" }
$wavDir = Join-Path $localBase "PagentOS/tts-measure/$Label"
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("pagentos-tts-measure-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
$container = "pagentos-tts-measure-" + [guid]::NewGuid().ToString("N").Substring(0, 12)
$exitCode = 0
try {
    New-Item -ItemType Directory -Path $work -Force | Out-Null

    # 2. image
    $inspect = Invoke-Native $Docker @("image", "inspect", $Image) 120
    if ($Rebuild -or $inspect.ExitCode -ne 0) {
        Write-Host "building $Image (pip --require-hashes) ..."
        $build = Invoke-Native $Docker @("build", "--no-cache", "-t", $Image, $toolDir) $BuildTimeoutSec
        if ($build.ExitCode -ne 0) {
            Stop-Measure 5 ("docker build failed (exit $($build.ExitCode)): " + ($build.StdErr + $build.StdOut).Trim())
        }
    }
    $check = Invoke-Native $Docker @("run", "--rm", "--network", "none", $Image, "selfcheck") 600
    if ($check.ExitCode -ne 0 -or $check.StdOut -notmatch "IMPORT_OK") {
        Stop-Measure 5 ("image selfcheck failed: " + ($check.StdOut + $check.StdErr).Trim())
    }
    Write-Host $check.StdOut.Trim()

    # 3. weights (the only networked run)
    $null = Invoke-Native $Docker @("volume", "create", $WeightsVolume) 120
    $fill = Invoke-Native $Docker @("run", "--rm", "--name", "$container-fill", "-v", "${WeightsVolume}:/models",
        $Image, "fill", "--models", "/models") $FillTimeoutSec
    if ($fill.ExitCode -ne 0) {
        $null = Invoke-Native $Docker @("rm", "-f", "$container-fill") 120
        Stop-Measure 3 ("weight fill failed (exit $($fill.ExitCode)): " + ($fill.StdOut + $fill.StdErr).Trim())
    }

    # 4. synthesis, offline
    $inputFile = Join-Path $work "input.jsonl"
    $made = Invoke-Native $Python ($pythonArgs + @("-m", "app.voice.tts_measure", "input", "--out", $inputFile)) 120 -WorkingDirectory $apiDir
    if ($made.ExitCode -ne 0) { Stop-Measure 6 ("tts_measure input failed: " + $made.StdErr.Trim()) }
    if (Test-Path -LiteralPath $wavDir) { Get-ChildItem -LiteralPath $wavDir -Filter "*.wav" | Remove-Item -Force }
    New-Item -ItemType Directory -Path $wavDir -Force | Out-Null
    $runArgs = @("run", "-i", "--rm", "--name", $container, "--network", "none", "--read-only",
        "--tmpfs", "/tmp", "--memory", $MemoryLimit, "--memory-swap", $MemoryLimit, "-e", "OMP_NUM_THREADS=$Threads")
    if ($Cpus) { $runArgs += @("--cpus", $Cpus) }
    if (-not $onWindows) { $runArgs += @("--user", "$(& /usr/bin/id -u):$(& /usr/bin/id -g)") }
    $runArgs += @("-v", "${WeightsVolume}:/models:ro", "-v", "${wavDir}:/out", $Image,
        "synth", "--models", "/models", "--out", "/out", "--threads", "$Threads", "--seed", "9", "--steps", "32")
    Write-Host "synthesizing $((Get-Content -LiteralPath $inputFile).Count) sentences as '$Label' (threads $Threads, cpus $(if ($Cpus) { $Cpus } else { 'all' })) ..."
    $synth = Invoke-Native $Docker $runArgs $TimeoutSec -StdIn ([System.IO.File]::ReadAllBytes($inputFile))
    if ($synth.TimedOut) {
        $null = Invoke-Native $Docker @("rm", "-f", $container) 120
        Stop-Measure 7 "the container passed its $TimeoutSec s deadline; killed and removed; no evidence written"
    }
    $null = Invoke-Native $Docker @("rm", "-f", $container) 120
    if ($synth.StdErr) { [Console]::Error.WriteLine($synth.StdErr.TrimEnd()) }
    if ($synth.ExitCode -ne 0) {
        Stop-Measure 3 ("synthesis failed (exit $($synth.ExitCode)); no evidence written: " + $synth.StdOut.Trim())
    }

    # 5. evidence
    $outputFile = Join-Path $work "container.jsonl"
    [System.IO.File]::WriteAllText($outputFile, $synth.StdOut, (New-Object System.Text.UTF8Encoding $false))
    $cpusLimit = if ($Cpus) { $Cpus } else { "" }
    $imageId = (Invoke-Native $Docker @("image", "inspect", "--format", "{{.Id}}", $Image) 120).StdOut.Trim()
    $merge = Invoke-Native $Python ($pythonArgs + @("-m", "app.voice.tts_measure", "merge", "--output", $outputFile,
        "--label", $Label, "--threads", "$Threads", "--cpus-limit", $cpusLimit, "--wav-dir", $wavDir,
        "--evidence-dir", $EvidenceDir, "--repo-root", $repoRoot, "--image", "$Image $imageId")) 300 -WorkingDirectory $apiDir
    if ($merge.ExitCode -ne 0) { Stop-Measure 6 ("tts_measure merge failed: " + ($merge.StdOut + $merge.StdErr).Trim()) }
    Write-Host $merge.StdOut.Trim()
    Write-Host "evidence: $(Join-Path $EvidenceDir 'tts-freya-measure.md'); WAVs: $wavDir"
}
finally {
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
}
exit $exitCode
