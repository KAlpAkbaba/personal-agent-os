<#
.SYNOPSIS
    Runtime qualification of the native factory's Android path (B49 items 475-477): the
    project the factory renders is built into an APK and an AAB, its unit tests run, and the
    APK is installed on an emulator, launched, and operated.

.DESCRIPTION
    B49 closed 474 (the project is rendered and checked as text) and left 475-477
    BLOCKED_PROVIDER because this machine had no JDK, Gradle, cmdline-tools or AVD. This script
    is the proof those items needed, and it is reproducible rather than a paragraph:

      1. render   - the REAL app.nativefactory.generator.render() writes counter-mobile
      2. build    - Gradle runs `test assembleDebug bundleRelease` on that tree
      3. tests    - the JUnit XML of both variants must hold >= 1 test and zero failures
      4. package  - the APK's badging must name the package, versionCode and launcher the
                    factory derived; the AAB must carry BundleConfig.pb and base/manifest
      5. boot     - the AVD boots with no window, under a deadline
      6. launch   - `am start -W` must report the activity, and it must be the resumed one
      7. operate  - '+' is tapped -Taps times; the counter text must read exactly -Taps
      8. stop     - the emulator is killed on every path, success or failure

    It downloads nothing, installs nothing into the system, and sets no persistent
    environment variable: every tool location is a parameter and lives in this process only.
    Item 478 (a physical phone) is outside it by design.

.PARAMETER JavaHome      A JDK 17 home.
.PARAMETER GradleHome    A Gradle >= 8.7 distribution (AGP 8.5.2 needs it).
.PARAMETER SdkRoot       The Android SDK (platform 33, build-tools, emulator, cmdline-tools).
.PARAMETER AvdName       An existing AVD (create one with cmdline-tools' avdmanager).
.PARAMETER AvdHome       Where that AVD lives, when not the default.
.PARAMETER WorkDir       Scratch directory for the rendered project and the proof files.
.PARAMETER EvidencePath  Where the JSON result is written.
.PARAMETER GradleUserHome  Gradle's dependency cache (default: under WorkDir).

.EXAMPLE
    powershell -File scripts\qualify-android-factory.ps1 -JavaHome E:\AI\toolchains\jdk-17.0.20.1+1 `
        -GradleHome E:\AI\toolchains\gradle-8.7 -AvdName pagentos-b49 -AvdHome E:\AI\toolchains\avd `
        -WorkDir E:\AI\toolchains\b49-proof -EvidencePath E:\AI\toolchains\b49-proof\result.json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $JavaHome,
    [Parameter(Mandatory)] [string] $GradleHome,
    [string] $SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [Parameter(Mandatory)] [string] $AvdName,
    [string] $AvdHome,
    [Parameter(Mandatory)] [string] $WorkDir,
    [Parameter(Mandatory)] [string] $EvidencePath,
    [ValidateRange(1, 20)] [int] $Taps = 2,
    [string] $GradleUserHome,
    [int] $BootTimeoutSeconds = 480,
    [int] $BuildTimeoutSeconds = 1800
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot 'services\api\.venv\Scripts\python.exe'
$adb = Join-Path $SdkRoot 'platform-tools\adb.exe'
$emulatorExe = Join-Path $SdkRoot 'emulator\emulator.exe'
$gradle = Join-Path $GradleHome 'bin\gradle.bat'
$project = Join-Path $WorkDir 'sayac'
$package = 'com.pagentos.sayac'

$result = [ordered]@{
    script       = 'scripts/qualify-android-factory.ps1'
    started_at   = (Get-Date).ToUniversalTime().ToString('o')
    requirements = @(475, 476, 477)
    gates        = [ordered]@{}
    passed       = $false
}

function Pass([string] $gate, $detail) { $result.gates[$gate] = [ordered]@{ result = 'PASS'; detail = $detail }; Write-Host "[PASS] $gate" }
function Fail([string] $gate, [string] $why) {
    $result.gates[$gate] = [ordered]@{ result = 'FAIL'; detail = $why }
    throw "[FAIL] ${gate}: $why"
}
function Sha256([string] $path) { (Get-FileHash $path -Algorithm SHA256).Hash.ToLower() }
# adb writes routine notices (no emulators yet, daemon started) to stderr; under Stop, Windows
# PowerShell 5.1 turns that into a terminating NativeCommandError. Only adb goes through here.
function Adb {
    $ErrorActionPreference = 'Continue'
    & $script:adb @args 2>$null
}

foreach ($tool in @($python, $adb, $emulatorExe, $gradle, (Join-Path $JavaHome 'bin\java.exe'))) {
    if (-not (Test-Path $tool)) { throw "missing tool: $tool (this script installs nothing)" }
}

# Process-local tool environment only. System32 first: the spawned shell's PATH on this machine
# can lack it, and the SDK's .bat launchers call findstr.
$env:JAVA_HOME = $JavaHome
$env:ANDROID_HOME = $SdkRoot
$env:GRADLE_USER_HOME = if ($GradleUserHome) { $GradleUserHome } else { Join-Path $WorkDir 'gradle-home' }
if ($AvdHome) { $env:ANDROID_AVD_HOME = $AvdHome }
$env:Path = "$JavaHome\bin;$env:SystemRoot\System32;$env:SystemRoot;$env:Path"
New-Item -ItemType Directory -Force $WorkDir | Out-Null

$emulator = $null
try {
    # 1 render ---------------------------------------------------------------------------------
    $renderer = Join-Path $WorkDir 'render.py'
    @'
import json, shutil, sys
from pathlib import Path
from app.nativefactory.generator import render
from app.nativefactory.spec import TEMPLATE_COUNTER_MOBILE, parse_spec
out = Path(sys.argv[1])
if out.exists():
    shutil.rmtree(out)
spec = parse_spec({"name": "Sayac", "title": "Sayaç", "template": TEMPLATE_COUNTER_MOBILE,
                   "targets": ["android_apk", "android_aab"], "version": "1.4.2",
                   "features": ["counter", "about"]})
files = render(spec).files
for f in files:
    p = out / f.path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f.text, encoding="utf-8", newline="\n")
print(json.dumps({"files": len(files)}))
'@ | Set-Content -Encoding utf8 $renderer
    Push-Location (Join-Path $repoRoot 'services\api')
    try { $rendered = & $python $renderer $project | ConvertFrom-Json } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $project 'app\build.gradle.kts'))) { Fail 'render' "render() exit $LASTEXITCODE" }
    Pass 'render' "$($rendered.files) files from app.nativefactory.generator.render()"

    # 2 build ----------------------------------------------------------------------------------
    $buildLog = Join-Path $WorkDir 'gradle-build.log'
    $build = Start-Process -FilePath $gradle -ArgumentList '--no-daemon', '--console=plain', 'test', 'assembleDebug', 'bundleRelease' `
        -WorkingDirectory $project -NoNewWindow -PassThru -RedirectStandardOutput $buildLog -RedirectStandardError "$buildLog.err"
    $null = $build.Handle  # without it Windows PowerShell 5.1 reports ExitCode as null
    if (-not $build.WaitForExit($BuildTimeoutSeconds * 1000)) { $build.Kill(); Fail 'build' "Gradle did not finish in $BuildTimeoutSeconds s" }
    if ($build.ExitCode -ne 0) { Fail 'build' "Gradle exit $($build.ExitCode); see $buildLog" }
    $apk = Join-Path $project 'app\build\outputs\apk\debug\app-debug.apk'
    $aab = Join-Path $project 'app\build\outputs\bundle\release\app-release.aab'
    foreach ($artifact in $apk, $aab) { if (-not (Test-Path $artifact)) { Fail 'build' "missing $artifact" } }
    Pass 'build' 'gradle test assembleDebug bundleRelease: exit 0'

    # 3 tests ----------------------------------------------------------------------------------
    $suites = [ordered]@{}
    foreach ($variant in 'testDebugUnitTest', 'testReleaseUnitTest') {
        $xmlFiles = @(Get-ChildItem (Join-Path $project "app\build\test-results\$variant") -Filter 'TEST-*.xml' -ErrorAction SilentlyContinue)
        if ($xmlFiles.Count -eq 0) { Fail 'tests' "$variant produced no JUnit XML" }
        $tests = 0; $bad = 0
        foreach ($f in $xmlFiles) { [xml] $junit = Get-Content $f.FullName; $tests += [int] $junit.testsuite.tests; $bad += [int] $junit.testsuite.failures + [int] $junit.testsuite.errors }
        if ($tests -lt 1 -or $bad -ne 0) { Fail 'tests' "$variant tests=$tests failures+errors=$bad" }
        $suites[$variant] = "$tests passed"
    }
    Pass 'tests' $suites

    # 4 package --------------------------------------------------------------------------------
    $aapt2 = Get-ChildItem (Join-Path $SdkRoot 'build-tools') -Recurse -Filter 'aapt2.exe' | Sort-Object FullName -Descending | Select-Object -First 1
    $badging = (& $aapt2.FullName dump badging $apk | Out-String)
    foreach ($needle in "package: name='$package'", "versionCode='1004003'", "launchable-activity: name='$package.MainActivity'") {
        if (-not $badging.Contains($needle)) { Fail 'package' "APK badging lacks $needle" }
    }
    $entries = (& (Join-Path $JavaHome 'bin\jar.exe') tf $aab | Out-String)
    foreach ($needle in 'BundleConfig.pb', 'base/manifest/AndroidManifest.xml') {
        if (-not $entries.Contains($needle)) { Fail 'package' "AAB lacks $needle" }
    }
    $signed = $entries -match 'META-INF/[^/\r\n]+\.(RSA|EC|DSA)'
    Pass 'package' ([ordered]@{
        apk = [ordered]@{ sha256 = (Sha256 $apk); bytes = (Get-Item $apk).Length; package = $package; version_code = 1004003; min_sdk = 23; target_sdk = 33 }
        aab = [ordered]@{ sha256 = (Sha256 $aab); bytes = (Get-Item $aab).Length; signed = [bool] $signed
                          note = 'the rendered project carries no signing config; a Play upload needs the owner keystore' }
    })

    # 5 boot -----------------------------------------------------------------------------------
    Adb start-server | Out-Null
    $emulator = Start-Process -FilePath $emulatorExe -PassThru -WindowStyle Hidden `
        -ArgumentList "-avd $AvdName -no-window -no-audio -no-snapshot -no-boot-anim -gpu swiftshader_indirect -accel on" `
        -RedirectStandardOutput (Join-Path $WorkDir 'emulator.log') -RedirectStandardError (Join-Path $WorkDir 'emulator.err.log')
    $null = $emulator.Handle  # keeps ExitCode readable after the process ends
    $deadline = (Get-Date).AddSeconds($BootTimeoutSeconds); $booted = $false; $bootStart = Get-Date
    while ((Get-Date) -lt $deadline) {
        if ($emulator.HasExited) { Fail 'boot' "emulator exited with $($emulator.ExitCode); see emulator.log" }
        $flag = (Adb -e shell getprop sys.boot_completed | Out-String).Trim()
        if ($flag -eq '1') { $booted = $true; break }
        Start-Sleep -Seconds 5
    }
    if (-not $booted) { Fail 'boot' "AVD $AvdName did not boot in $BootTimeoutSeconds s" }
    $release = (Adb -e shell getprop ro.build.version.release | Out-String).Trim()
    Pass 'boot' "AVD $AvdName, Android $release, $([int]((Get-Date) - $bootStart).TotalSeconds) s"

    # 6 launch ---------------------------------------------------------------------------------
    $install = (Adb -e install -r $apk | Out-String)
    if (-not $install.Contains('Success')) { Fail 'launch' "install: $($install.Trim())" }
    $start = (Adb -e shell am start -W -n "$package/.MainActivity" | Out-String)
    if (-not $start.Contains('Status: ok')) { Fail 'launch' "am start: $($start.Trim())" }
    Start-Sleep -Seconds 2
    $resumed = (Adb -e shell dumpsys activity activities | Select-String 'topResumedActivity' | Select-Object -First 1 | Out-String).Trim()
    if (-not $resumed.Contains("$package/.MainActivity")) { Fail 'launch' "resumed activity is not ours: $resumed" }
    $pidText = (Adb -e shell pidof $package | Out-String).Trim()
    $launchShot = Join-Path $WorkDir 'sayac-launched.png'
    cmd /c "`"$adb`" -e exec-out screencap -p > `"$launchShot`""
    Pass 'launch' ([ordered]@{ resumed = "$package/.MainActivity"; pid = $pidText; screenshot_sha256 = (Sha256 $launchShot) })

    # 7 operate --------------------------------------------------------------------------------
    function Read-Ui {
        Adb -e shell uiautomator dump /sdcard/pagentos-ui.xml | Out-Null
        [xml] (Adb -e shell cat /sdcard/pagentos-ui.xml | Out-String)
    }
    function Counter-Text([xml] $ui) {
        # the counter is the TextView under the app bar; the title TextView is 'Sayaç'
        $views = @($ui.SelectNodes('//node') | Where-Object { $_.class -eq 'android.widget.TextView' -and $_.text -match '^\d+$' })
        if ($views.Count -ne 1) { return $null }
        $views[0].text
    }
    $ui = Read-Ui
    $before = Counter-Text $ui
    if ($before -ne '0') { Fail 'operate' "counter before tapping is '$before', expected '0'" }
    $plus = @($ui.SelectNodes('//node') | Where-Object { $_.class -eq 'android.widget.Button' -and $_.text -eq '+' })[0]
    if (-not $plus -or $plus.bounds -notmatch '\[(\d+),(\d+)\]\[(\d+),(\d+)\]') { Fail 'operate' "no '+' button in the UI tree" }
    $tapX = ([int] $Matches[1] + [int] $Matches[3]) / 2; $tapY = ([int] $Matches[2] + [int] $Matches[4]) / 2
    for ($i = 0; $i -lt $Taps; $i++) { Adb -e shell input tap $tapX $tapY | Out-Null; Start-Sleep -Milliseconds 700 }
    $after = Counter-Text (Read-Ui)
    if ($after -ne "$Taps") { Fail 'operate' "after $Taps taps the counter reads '$after'" }
    $operatedShot = Join-Path $WorkDir 'sayac-operated.png'
    cmd /c "`"$adb`" -e exec-out screencap -p > `"$operatedShot`""
    Pass 'operate' ([ordered]@{ taps = $Taps; counter_before = $before; counter_after = $after; screenshot_sha256 = (Sha256 $operatedShot) })

    $result.passed = $true
}
catch {
    $result.error = "$_"
    Write-Host "$_"
}
finally {
    # 8 stop -----------------------------------------------------------------------------------
    if ($emulator) {
        Adb -e emu kill | Out-Null
        if (-not $emulator.WaitForExit(30000)) { try { $emulator.Kill() } catch { } }
        $result.gates['stop'] = [ordered]@{ result = 'PASS'; detail = "emulator pid $($emulator.Id) stopped" }
    }
    $result.finished_at = (Get-Date).ToUniversalTime().ToString('o')
    $result | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $EvidencePath
}

if ($result.passed) { Write-Host "ANDROID FACTORY QUALIFIED: $($result.gates.Count) gates passed"; exit 0 }
exit 1
