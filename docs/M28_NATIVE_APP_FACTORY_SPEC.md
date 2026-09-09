# M28 — Native Desktop + Mobile Application Factory

Status: ACTIVE (owner master directive 2026-09-07, M28 section — the last milestone; no M29). Decision record: ADR-0095. Toolchain measured 2026-09-08 and re-measured 2026-09-09 before implementation: `docs/evidence/m28-toolchain-2026-09-09.json`.
Predecessors: M23 App Factory (`AppSpec`, templates as data, the `ProjectFiles` policy, the device's `projects` family with Job Object runs, the fixed command allowlist, "free text never lands in code"), M19 Digital Operator (`app.launch`, `window.*`, `ui.*`, `keyboard/pointer` under authority, the typing guard), M26 Executive Autonomy (a durable graph for the long lifecycle), M25 (bounded batch runs of installed toolchains), M22 (independent readers for artifacts).

The owner's rule, in one line: **a distributable application exists when a real build produced a real artifact on this machine, the artifact was installed or launched, its window and its core workflow were driven and observed through the operator, its logs were read, and every claim about a platform the toolchain cannot reach is classified honestly as not built.**

## 1. What the toolchain allows here (measured 2026-09-08, RE-MEASURED 2026-09-09 before any of it was designed against: `docs/evidence/m28-toolchain-2026-09-09.json`)

Two findings of the re-measurement changed this document, and both would otherwise have
shaped code:

- **There is no .NET MAUI workload** (`dotnet workload list` lists none). Installing one is
  a download, which the assistant does not start. `dotnet_maui` is therefore NOT a stack
  this milestone has, and §3's rule for "Windows and Android together" produces two
  projects from one spec instead.
- **`pefile` is not importable.** §4 promised the EXE's PE header "read by `pefile`";
  adding a dependency to the Cloud Core image for one function is exactly the trade M27
  refused for numpy, and refusing it there while taking it here would be incoherent. The
  independent reader parses the PE header with the **standard library**.
- The good news from the same run: **`aapt2` is present** (build-tools 33.0.0), so an APK
  can be validated independently the moment one can be built, and `makeappx`/`signtool`
  are both present under Windows Kits 10.0.26100.0.

- **Windows**: .NET SDK 10.0.400 with the `wpf`/`winforms`/`console` templates — a real EXE through `dotnet publish -c Release -r win-x64 --self-contained` with no Visual Studio (a FOLDER publish, not single-file: measured 2026-09-09, `PublishSingleFile=true` needs `Microsoft.NET.ILLink.Tasks`, which does not restore on this machine - NU1100 - and the folder publish is what `windows_portable` zips anyway); a **portable package** (a zip of the publish folder with a manifest); an **MSIX** through the Windows Kits' `makeappx.exe` (present) signed with a self-signed test certificate created for the run (installs only where the test certificate is trusted — the sideload install is the owner's item unless `Add-AppxPackage` accepts it unelevated for the current user; the lab measures). Inno Setup and WiX are absent: an MSI is out of scope until one is installed (honest). Rust `cargo` and Node are present, so **Tauri** is a justified second stack for a web-UI desktop app (M23's templates as the UI); Electron is not chosen (heavier, no advantage here). Stack selection is a rule from the requirements (spec §3), never one framework for everything. **MAUI is not available here** (no workload installed), so it appears in no rule below.
- **Android**: the SDK exists (`adb`, the emulator, build-tools 30.0.3/33.0.0, platforms 32/33, system images for API 23 and 33) but there is **no JDK** on this machine (no `java`, no `JAVA_HOME`, no Android Studio, no `cmdline-tools`, no AVD). An APK/AAB build (Gradle, `javac`/`kotlinc`, `apksigner` runs on Java, `avdmanager` too) is impossible until a JDK is installed — **owner item 33** (install Android Studio or a JDK 17; the assistant never starts a download by itself). M28 ships the Android provider, the project template (a minimal Kotlin/Gradle app — the MAUI Android head is not an option, there being no MAUI workload), the emulator driver (`emulator -avd <name> -no-window -no-audio -gpu swiftshader_indirect`, `adb wait-for-device`, `adb install`, `adb shell am start`, `adb logcat`, UI through `uiautomator dump` — semantic, not pixels), and a lab that runs the real toolchain when present and reports `dependency_unavailable` with the detection facts otherwise; the Android marks are PROVEN_PROXY until item 33 flips the same lab to PROVEN_REAL with no code change. The directive's "emulator proof should not wait for the owner" is honoured as far as the toolchain allows — the emulator binary and images exist; only the build and the AVD creation need Java.
- **iOS**: no macOS, no Xcode — no IPA, no native build, ever claimed here, and with no MAUI workload there is not even a shared head to compile. iOS is classified **NOT BUILT** and the voice says so; nothing is generated that could be mistaken for progress towards it.

## 2. The application spec (M23's `AppSpec`, extended as data)

`app/nativefactory/spec.py` — `NativeAppSpec`: `name`, `targets[] ⊆ {windows_exe, windows_portable, windows_msix, android_apk, android_aab, ios_project}`, `stack ∈ {dotnet_wpf, dotnet_winforms, tauri, android_kotlin}` (no `dotnet_maui`: the workload is not installed and the assistant installs nothing) (chosen by §3 unless the owner names one), `template ∈ {notes_desktop, counter_mobile, task_tracker_native}` (built-in templates as files with `{{SLOT}}` markers — the M23 discipline: free text never lands in code; names, ids and versions are closed-alphabet; the UI strings are Turkish resources, escaped by the resource format), `version` (semver, bounded), `persistence ∈ {none, local_file}`, `screens[]` (from the template's catalogue), the closed `features[]`.

## 3. Stack selection (a rule, recorded on the row)

`windows_exe|portable|msix` + a form UI → `dotnet_wpf`; a web-technology UI (an M23 web app promoted to desktop) → `tauri`; `android_*` → `android_kotlin` (a JDK being present); a request naming both Windows and Android → **two projects** (WPF + Kotlin) from the one spec, MAUI not being available here; iOS → refused with the reason, nothing generated. The choice and its reason are on the receipt ("WPF seçtim: Windows masaüstü, form arayüzü, kurulu araç .NET 10").

## 4. The lifecycle (the directive's, as an M26 task graph)

requirements → the spec → architecture (the stack rule) → implementation (the template rendered, validated by the `ProjectFiles` policy — now also `.cs`/`.csproj`/`.kt`/`.gradle` as text with the same no-free-text-in-code splice guard) → tests (the template's unit tests: `dotnet test` / Gradle test) → UI testing (below) → bug fix → regression → **release build** (`dotnet publish` / `gradle assembleRelease|bundleRelease`) → packaging (portable zip, MSIX via `makeappx`, the APK signed with a run-local keystore) → install/launch → smoke test → **artifact validation** (an independent reader: the EXE's PE header and version resource read with the **standard library** (`struct` over the DOS/NT headers and the `VS_VERSIONINFO` resource) — no new dependency for one function, the MSIX opened as a zip and its `AppxManifest.xml` parsed, the APK opened as a zip with `AndroidManifest.xml` decoded by `aapt2 dump badging`, sha256 + size on the receipt) → version metadata (the spec's version stamped into the assembly/manifest and read back from the artifact) → the final candidate (a row with every artifact's path, hash and the smoke evidence). The graph runs as an M26 `ExecutiveWorkflow` with `nativefactory.*` step kinds, so it is pausable, resumable and honest when partial.

## 5. Running and driving the result (the device)

- The builds run through the device's M23 runner as bounded Job Object children under a new authorised root (`%USERPROFILE%\Documents\PagentOS Projects\native`): the allowlist gains `dotnet <build|test|publish> …` under the root, `makeappx pack …`, `signtool`/`certutil` for the run-local certificate, the Android `gradlew`/`adb`/`emulator` argv shapes under the SDK root and the project root; Unity-style bounds (≤ 20 min, ≤ 4 GiB).
- **Windows qualification** (real on this machine): launch the built EXE through M19 `app.launch` (an authorised root path), verify the process (pid, exe path) and the window (`window.list` by process), drive the core workflow through `ui.*` (UI Automation: type a note, press save, read the list), close and relaunch, verify persistence (the local file reappears in the list), read the app's own log file under its data dir, fix crashes (the M18.4 closed loop over the log), rebuild, rerun. If an MSIX was built: install for the current user (`Add-AppxPackage` after trusting the run-local certificate in the CurrentUser store — no elevation; measured), launch by AUMID, verify, `Remove-AppxPackage`, verify cleanup.
- **Android qualification** (real when item 33 is done; until then the lab proves the shape with the real `emulator`/`adb` binaries and the honest refusal at the build step): create an AVD (API 33 x86_64 image present), boot headless, `adb install`, `am start`, `uiautomator dump` to find the counter button and press it through `adb shell input tap` on the node's bounds (semantic first), read `logcat` for crashes, rotate (`settings put system user_rotation`), relaunch, verify state, fix, rebuild, retest. A physical device is never used without explicit owner authorisation.

## 6. Voice (the ONE router; corpus category `nativeapps`)

Intents `NATIVE_CREATE_WINDOWS` ("Bana Windows için masaüstü uygulaması yap."), `NATIVE_BUILD_EXE` ("Bunu EXE olarak çıkar."), `NATIVE_BUILD_INSTALLER` ("Kurulum dosyasını oluştur."), `NATIVE_CREATE_ANDROID` ("Android sürümünü yap.", "Bunun Android sürümünü yap."), `NATIVE_BUILD_APK` ("APK üret."), `NATIVE_EMULATOR_OPEN` ("Uygulamayı emülatörde aç."), `NATIVE_CHECK` ("Çalışıyor mu kontrol et."), `NATIVE_FIX` ("Hata varsa düzelt."), `NATIVE_REBUILD` ("Yeni sürümü build et."); tools `native.create | build | package | install | launch | check | fix | rebuild`; receipts read the artifact back (its name, size, sha256 prefix, version, the smoke result: "EXE hazır: Notlarim.exe, 68 MB, sürüm 0.1.0; açıldı, pencere göründü, not kaydedildi ve yeniden açılınca duruyordu"); TTS progress lines at each lifecycle arrow from rows ("Derleniyor" / "Paketleniyor" / "Kuruluyor"). Negatives: an iOS build request → the honest "Bu bilgisayarda macOS/Xcode yok; iOS için yalnızca paylaşılan proje hazırlanabilir", an Android build without a JDK → the honest refusal naming item 33, "Kurulumu kaldır" of anything not installed by this run → refused, forbidden side effects 0 (no installs outside the run-local scope, nothing of the owner's touched).

## 7. Cross-milestone object focus (the directive's section)

`object_focus` gains the kinds `executable`, `mobile_build`, `creative_document`, `scene`, `capability_candidate`, `source_repo` (some already exist from M23–M27); "Bunu EXE yap." / "Bunun Android sürümünü yap." / "Bu uygulamadaki bug'ı düzelt." / "Bir öncekine dön." resolve through ids on the stack, never fuzzy titles; the focus is persisted current/previous as M19 already does.

## 8. Marks sought

Windows EXE + portable + (MSIX where the sideload install is possible unelevated) PROVEN_REAL on this machine through the operator; Android PROVEN_PROXY until item 33, then PROVEN_REAL by the same lab; iOS NOT BUILT (classified); the lifecycle as an M26 graph PROVEN_AUTOMATED; voice PROVEN_AUTOMATED; the Living Core (UI contract v13 `native.build`, the Cockpit "Yerel Uygulamalar" panel) PROVEN_AUTOMATED; the Cloud Core PROVEN_REAL (release). M28 completes only with actual artifacts generated and machine-verified where the local toolchain supports them — the EXE and its smoke evidence are the minimum.

## 9. Security

- No download or install of toolchains by the assistant; missing toolchains are owner items.
- Builds and installs are confined to the `native` root, a run-local certificate/keystore (never the owner's), the current-user scope, and a Job Object; nothing system-wide changes; uninstall is verified.
- No free text reaches code, project files or manifests except through the resource formats' own escaping; the templates are fixed files; the coding-model seam (the directive's worker strategy: Planner/Coder/Fixer roles over real compiler output) is inert in tests and proposes only diffs that go through the same policy and build gates.
- The operator drives only windows of the process the run launched; the M19 typing guard and authority stand.
