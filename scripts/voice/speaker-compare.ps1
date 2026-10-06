<#
.SYNOPSIS
  Speaker-engine measurement (speaker-engine-measure): MEASUREMENT ONLY. CAM++ / ERes2NetV2 /
  CAM++ VoxCeleb speaker embeddings and the pyannote-3.0 segmenter (sherpa-onnx) in their own
  container. Nothing is wired: app/voice/speaker.py, the conversation store, every profile,
  Settings and the web are untouched; services/api gets no dependency.

.DESCRIPTION
  1. Refuses to start unless this machine is on the allow-list (the home PC and the Cloud
     Core; never the employer's office PC) - before any docker call.
  2. The recordings:
     -FromCore downloads the owner's own /voice/measure sentences exactly as
     stt-compare.ps1 -FromCore does (scripts\lib\VoiceMeasurementCore.ps1): an owner session,
     every file's sha256 checked against the Core's - one mismatch stops the run before any
     container - into a fresh temp folder.
     -Folder <dir> holds the second-person material and nothing else: guest.wav (ONE consenting
     person alone, 60-180 s, 16 kHz mono PCM16), optionally owner.wav (the owner alone),
     conversation.wav (a natural two-person recording) with reference.json, and - without
     -FromCore - the owner's sentences as ev-NN.wav / ofis-NN.wav. Any other file stops the
     run (exit 2). consent.json {guest_consent: true, consent_date} is REQUIRED for anything
     of the guest to be read: without it guest.wav, conversation.wav and reference.json are
     never opened, copied or mounted, and the guest rows say so. The guest has no name here:
     the label is "konuk".
  3. Builds the image from tools/speaker-measure (pinned base digest, pip --require-hashes;
     content-addressed tag pagentos-speaker-measure:<12 hex>), fills the named model volume
     once WITH network (fetch_models.py: pinned URLs, size + sha256; a mismatch is exit 3).
  4. Every measurement run is --network none --read-only --user 10001 with the memory ceiling
     and no swap; -Cpus / -Threads give the 'cpx32-bicimi' shape. One container per embedding
     model (its own load time and peak memory). Every wait has a deadline; a container past it
     is killed and removed.
  5. python -m app.voice.speaker_measure merge writes <EvidenceDir>\speaker-measure.json + .md
     (a re-run of a label replaces only that label) and the two listening timelines.
  The listening files go to %LOCALAPPDATA%\PagentOS\speaker-measure\<label>\ (Linux:
  ~/.local/share/...), never inside the repository. The staged recordings and every temp
  folder are removed at the end, success or not. Vectors never leave the container's memory.

.EXAMPLE
  .\scripts\voice\speaker-compare.ps1 -Label ev-pc -FromCore -Folder D:\konuk
  .\scripts\voice\speaker-compare.ps1 -Label cpx32-bicimi -Cpus 4 -Threads 4 -FromCore -Folder D:\konuk
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidatePattern("^[a-z0-9][a-z0-9-]{0,39}$")][string]$Label,
    [string]$Folder = "",
    [switch]$FromCore,
    [string]$CoreUrl = "",
    [ValidatePattern("^(ev|ofis)?$")][string]$Place = "",
    [string]$InstallRoot = "",
    [string]$Models = "campplus-zh-en-advanced,eres2netv2-zh-cn,campplus-voxceleb",
    [string]$Cpus = "",
    [int]$Threads = 0,
    [switch]$SyntheticVoices,
    [string]$EvidenceDir = "",
    [string]$Docker = "",
    [string]$Python = "",
    [string]$Image = "",
    [string]$ModelsVolume = "pagentos-speaker-models",
    [string]$MemoryLimit = "4g",
    [int]$TimeoutSec = 3600,
    [int]$BuildTimeoutSec = 1800,
    [int]$FillTimeoutSec = 1800,
    [double]$MinFreeGB = 5,
    [string[]]$AllowedHosts = @("MAIL", "pagentos-core"),
    [switch]$Rebuild
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$onWindows = [System.IO.Path]::DirectorySeparatorChar -eq '\'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$toolDir = Join-Path $repoRoot "tools/speaker-measure"
$apiDir = Join-Path $repoRoot "services/api"
if (-not $EvidenceDir) { $EvidenceDir = Join-Path $repoRoot "docs/evidence" }
if ($Threads -le 0) { $Threads = [Environment]::ProcessorCount }
if (-not $InstallRoot -and $env:ProgramFiles) { $InstallRoot = Join-Path $env:ProgramFiles "PagentOS\agent" }
if (-not $Image) {
    # Content-addressed tag: a changed Dockerfile / lock / script is a new image, never a stale one.
    $parts = foreach ($name in @("Dockerfile", "requirements.txt", "fetch_models.py", "measure.py")) {
        (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $toolDir $name)).Hash
    }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $digest = -join ($sha.ComputeHash([System.Text.Encoding]::ASCII.GetBytes($parts -join "`n")) | ForEach-Object { $_.ToString("x2") })
    $Image = "pagentos-speaker-measure:" + $digest.Substring(0, 12)
}

. (Join-Path $repoRoot "scripts/lib/VoiceMeasurementCore.ps1")

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
            $kill = New-Object System.Diagnostics.ProcessStartInfo
            $kill.FileName = Join-Path $env:SystemRoot "System32\taskkill.exe"
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
    [Console]::Error.WriteLine("speaker-compare: $Message")
    exit $Code
}

# Arguments every MEASUREMENT container carries: offline, read-only, uid 10001, no swap.
function Get-OfflineRunArgs([string]$Name) {
    $a = @("run", "--rm", "--name", $Name, "--network", "none", "--read-only", "--tmpfs", "/tmp",
        "--user", "10001", "--memory", $MemoryLimit, "--memory-swap", $MemoryLimit, "-e", "OMP_NUM_THREADS=$Threads")
    if ($Cpus) { $a += @("--cpus", $Cpus) }
    return , $a
}

# 1. allow-list, before anything touches docker
$hostName = if ($env:COMPUTERNAME) { $env:COMPUTERNAME } else { [Environment]::MachineName }
if (@($AllowedHosts | Where-Object { $_ -ieq $hostName }).Count -eq 0) {
    Stop-Measure 4 "host '$hostName' is not on the allow-list ($($AllowedHosts -join ', ')); never run this on the office PC"
}

# 2a. the folder: only the names this measurement reads; consent decides what of the guest is read
$sentencePattern = '^(ev|ofis)-\d{2}\.wav$'
$allowedPattern = '^((ev|ofis)-\d{2}\.wav|owner\.wav|guest\.wav|conversation\.wav|reference\.json|consent\.json)$'
$consent = $false
$folderFiles = @()
if (-not $FromCore -and -not $Folder) { Stop-Measure 2 "give -FromCore (the owner's sentences on the Cloud Core) and/or -Folder <dir>" }
if ($Folder) {
    if (-not (Test-Path -LiteralPath $Folder -PathType Container)) { Stop-Measure 2 "folder not found: $Folder" }
    $Folder = (Resolve-Path -LiteralPath $Folder).Path
    $folderFiles = @(Get-ChildItem -LiteralPath $Folder -Force)
    foreach ($entry in $folderFiles) {
        if ($entry.PSIsContainer -or $entry.Name -cnotmatch $allowedPattern) {
            Stop-Measure 2 "'$($entry.Name)' is not a file this measurement reads (allowed: ev-NN.wav, ofis-NN.wav, owner.wav, guest.wav, conversation.wav, reference.json, consent.json); nothing was read"
        }
        if ($FromCore -and $entry.Name -cmatch $sentencePattern) {
            Stop-Measure 2 "-FromCore takes the owner's sentences from the Cloud Core; '$($entry.Name)' in the folder would mix two sets"
        }
    }
    $consentFile = Join-Path $Folder "consent.json"
    if (Test-Path -LiteralPath $consentFile -PathType Leaf) {
        try {
            $record = [System.IO.File]::ReadAllText($consentFile, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
            $names = @($record.PSObject.Properties | ForEach-Object { $_.Name })
            $given = ($names -contains "guest_consent") -and ($record.guest_consent -is [bool]) -and $record.guest_consent
            $dated = ($names -contains "consent_date") -and ([string]$record.consent_date).Trim() -ne ""
            $consent = [bool]($given -and $dated)
        }
        catch { $consent = $false }
    }
    if (-not $consent) {
        Write-Host "no valid consent.json: the guest's recordings are not read (rows: no consent, not measured)"
    }
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
$env:PYTHONIOENCODING = "utf-8"

if ($MinFreeGB -gt 0) {
    $drive = New-Object System.IO.DriveInfo ($(if ($onWindows) { "C" } else { "/" }))
    $freeGB = [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
    if ($freeGB -lt $MinFreeGB) { Stop-Measure 2 "only $freeGB GB free on $($drive.Name) (Docker's disk); need $MinFreeGB GB" }
}

$localBase = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME ".local/share" }
$listenDir = Join-Path $localBase "PagentOS/speaker-measure/$Label"
$container = "pagentos-speaker-measure-" + [guid]::NewGuid().ToString("N").Substring(0, 12)
$work = ""
try {
    $work = New-VoiceCoreFolder -Prefix "pagentos-speaker-compare-" -RepoRoot $repoRoot
    $staged = Join-Path $work "in"
    $spliceDir = Join-Path $work "splice"
    New-Item -ItemType Directory -Path $staged, $spliceDir | Out-Null

    # 2b. the owner's sentences from the Cloud Core (sha256-checked before any container)
    $sentenceFiles = @($folderFiles | Where-Object { $_.Name -cmatch $sentencePattern } | ForEach-Object { $_.FullName })
    if ($FromCore) {
        . (Join-Path $repoRoot "scripts/lib/HttpJson.ps1")
        . (Join-Path $repoRoot "scripts/lib/SecretStore.ps1")
        . (Join-Path $repoRoot "scripts/lib/AgentUpdate.ps1")
        $coreDir = Join-Path $work "core"
        New-Item -ItemType Directory -Path $coreDir | Out-Null
        $headers = @{}
        try {
            $base = Resolve-VoiceCoreUrl -CoreUrl $CoreUrl -InstallRoot $InstallRoot
            Write-Host "recordings: the Cloud Core ($base)$(if ($Place) { ", place $Place" })"
            $headers = Get-VoiceCoreHeaders -Base $base -Label "speaker-compare"
            $listing = Get-VoiceMeasurementListing -Base $base -Headers $headers -Place $Place
            $items = @($listing.Items)
            if ($items.Count -gt 0) {
                Save-VoiceMeasurementAudio -Base $base -Headers $headers -Items $items -ByFile $listing.ByFile -Folder $coreDir -MismatchNote "nothing was measured"
            }
            Write-Host ("downloaded {0} recording(s), every sha256 matches the Core's" -f $items.Count)
        }
        catch {
            Write-Host "!! the recordings could not be taken from the Cloud Core: $($_.Exception.Message)"
            exit 3
        }
        finally { $headers = @{} }
        $sentenceFiles = @(Get-ChildItem -LiteralPath $coreDir -Filter "*.wav" | ForEach-Object { $_.FullName })
    }

    # 2c. stage under neutral names: sahip-NN, sahip-uzun, and - only with consent - konuk, konusma
    $n = 0
    foreach ($file in @($sentenceFiles | Sort-Object)) {
        $n++
        [System.IO.File]::Copy($file, (Join-Path $staged ("sahip-{0:D2}.wav" -f $n)))
    }
    if ($Folder -and (Test-Path -LiteralPath (Join-Path $Folder "owner.wav") -PathType Leaf)) {
        [System.IO.File]::Copy((Join-Path $Folder "owner.wav"), (Join-Path $staged "sahip-uzun.wav"))
    }
    if ($consent) {
        foreach ($pair in @(@("guest.wav", "konuk.wav"), @("conversation.wav", "konusma.wav"), @("reference.json", "referans.json"))) {
            $source = Join-Path $Folder $pair[0]
            if (Test-Path -LiteralPath $source -PathType Leaf) {
                [System.IO.File]::Copy($source, (Join-Path $staged $pair[1]))
            }
        }
    }
    $prepared = Invoke-Native $Python ($pythonArgs + @("-m", "app.voice.speaker_measure", "prepare",
        "--in", $staged, "--work", $work, "--models", $Models)) 300 -WorkingDirectory $apiDir
    if ($prepared.ExitCode -ne 0) { Stop-Measure 2 ("the recordings were refused: " + ($prepared.StdErr + $prepared.StdOut).Trim()) }
    $selected = @($Models.Split(",") | Where-Object { $_ })

    # 3. image and models (the fill is the only networked run)
    $inspect = Invoke-Native $Docker @("image", "inspect", $Image) 120
    if ($Rebuild -or $inspect.ExitCode -ne 0) {
        Write-Host "building $Image (pip --require-hashes) ..."
        $build = Invoke-Native $Docker @("build", "--no-cache", "-t", $Image, $toolDir) $BuildTimeoutSec
        if ($build.ExitCode -ne 0) {
            Stop-Measure 5 ("docker build failed (exit $($build.ExitCode)): " + ($build.StdErr + $build.StdOut).Trim())
        }
    }
    $check = Invoke-Native $Docker ((Get-OfflineRunArgs "$container-check") + @($Image, "selfcheck")) 600
    if ($check.ExitCode -ne 0 -or $check.StdOut -notmatch "SELFCHECK_OK") {
        $null = Invoke-Native $Docker @("rm", "-f", "$container-check") 120
        Stop-Measure 5 ("image selfcheck failed: " + ($check.StdOut + $check.StdErr).Trim())
    }
    Write-Host $check.StdOut.Trim()
    $null = Invoke-Native $Docker @("volume", "create", $ModelsVolume) 120
    $fill = Invoke-Native $Docker @("run", "--rm", "--name", "$container-fill", "--read-only", "--tmpfs", "/tmp",
        "--user", "10001", "-v", "${ModelsVolume}:/models", $Image, "fill", "--models", "/models") $FillTimeoutSec
    if ($fill.ExitCode -ne 0) {
        $null = Invoke-Native $Docker @("rm", "-f", "$container-fill") 120
        Stop-Measure 3 ("model fill failed (exit $($fill.ExitCode)): " + ($fill.StdOut + $fill.StdErr).Trim())
    }

    # 4a. the spliced conversation (offline), when the owner and a consenting guest are both there
    if (Test-Path -LiteralPath (Join-Path $staged "splice.json")) {
        $spliceArgs = (Get-OfflineRunArgs "$container-splice") + @("-v", "${staged}:/in:ro", "-v", "${spliceDir}:/out",
            $Image, "splice", "--plan", "/in/splice.json", "--out", "/out/eklenti.wav")
        $splice = Invoke-Native $Docker $spliceArgs 600
        if ($splice.ExitCode -ne 0) {
            $null = Invoke-Native $Docker @("rm", "-f", "$container-splice") 120
            Stop-Measure 5 ("splice failed (exit $($splice.ExitCode)): " + ($splice.StdErr + $splice.StdOut).Trim())
        }
    }

    # 4b. one offline container per embedding model
    foreach ($model in $selected) {
        $jobs = Join-Path $work "jobs-$model.jsonl"
        $name = "$container-$model"
        $offline = Get-OfflineRunArgs $name
        $runArgs = @("run", "-i") + $offline[1..($offline.Count - 1)]
        $runArgs +=@("-v", "${ModelsVolume}:/models:ro", "-v", "${staged}:/in:ro", "-v", "${spliceDir}:/splice:ro", $Image,
            "measure", "--models-dir", "/models", "--model", $model, "--threads", "$Threads", "--segmenter")
        Write-Host "measuring $model as '$Label' (threads $Threads, cpus $(if ($Cpus) { $Cpus } else { 'all' })) ..."
        $measured = Invoke-Native $Docker $runArgs $TimeoutSec -StdIn ([System.IO.File]::ReadAllBytes($jobs))
        $null = Invoke-Native $Docker @("rm", "-f", $name) 120
        if ($measured.StdErr) { [Console]::Error.WriteLine($measured.StdErr.TrimEnd()) }
        if ($measured.ExitCode -eq 3) {
            Stop-Measure 3 ("model files are not the pinned bytes; nothing measured, no evidence written: " + $measured.StdOut.Trim())
        }
        if ($measured.TimedOut -or $measured.ExitCode -ne 0) {
            $facts = @{ exit = $measured.ExitCode; timed_out = [bool]$measured.TimedOut } | ConvertTo-Json -Compress
            [System.IO.File]::WriteAllText((Join-Path $work "fail-$model.json"), $facts, (New-Object System.Text.UTF8Encoding $false))
            continue
        }
        [System.IO.File]::WriteAllText((Join-Path $work "out-$model.jsonl"), $measured.StdOut, (New-Object System.Text.UTF8Encoding $false))
    }

    # 5. listening files (outside the repository) and evidence
    if (Test-Path -LiteralPath $listenDir) {
        Get-ChildItem -LiteralPath $listenDir -File | Where-Object { $_.Name -match '\.(wav|txt)$' } | Remove-Item -Force
    }
    New-Item -ItemType Directory -Path $listenDir -Force | Out-Null
    foreach ($pair in @(@((Join-Path $spliceDir "eklenti.wav"), "eklenti.wav"), @((Join-Path $staged "konusma.wav"), "konusma.wav"))) {
        if (Test-Path -LiteralPath $pair[0]) { [System.IO.File]::Copy($pair[0], (Join-Path $listenDir $pair[1]), $true) }
    }
    $imageId = (Invoke-Native $Docker @("image", "inspect", "--format", "{{.Id}}", $Image) 120).StdOut.Trim()
    $merge = Invoke-Native $Python ($pythonArgs + @("-m", "app.voice.speaker_measure", "merge", "--work", $work,
        "--label", $Label, "--threads", "$Threads", "--cpus-limit", $Cpus, "--evidence-dir", $EvidenceDir,
        "--listen-dir", $listenDir, "--repo-root", $repoRoot, "--image", "$Image $imageId",
        "--consent", $(if ($consent) { "yes" } else { "no" }), "--synthetic", $(if ($SyntheticVoices) { "yes" } else { "no" }),
        "--models", $Models)) 300 -WorkingDirectory $apiDir
    if ($merge.ExitCode -ne 0) { Stop-Measure 6 ("speaker_measure merge failed: " + ($merge.StdOut + $merge.StdErr).Trim()) }
    # the verdict is Turkish and the child wrote UTF-8: through Console.Out with a UTF-8 encoding
    # (5.1's host writer keeps its start-up code page on a redirected stdout, as stt-compare notes)
    $previousEncoding = $null
    try { $previousEncoding = [Console]::OutputEncoding; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { $previousEncoding = $null }
    [Console]::Out.WriteLine($merge.StdOut.Trim())
    [Console]::Out.Flush()
    if ($null -ne $previousEncoding) { try { [Console]::OutputEncoding = $previousEncoding } catch { } }
    Write-Host "evidence:$(Join-Path $EvidenceDir 'speaker-measure.md'); listening: $listenDir"
}
finally {
    if ($work -and -not (Remove-CoreFolder -Path $work)) {
        [Console]::Error.WriteLine("speaker-compare: the staged recordings could not be removed: $work - delete it by hand")
    }
}
exit 0
