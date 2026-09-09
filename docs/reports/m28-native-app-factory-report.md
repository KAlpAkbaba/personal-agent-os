# M28 Native Application Factory — milestone report (2026-09-09, IN PROGRESS)

Owner directive: master directive "CLOSE M18.4 AND COMPLETE M19 -> M28" (M28 section: the last milestone; there is no M29). Decision record: ADR-0095. Spec: `docs/M28_NATIVE_APP_FACTORY_SPEC.md`. QUALIFICATION Stage 26 (not yet written — see §"What closing still needs").

**Status: the Windows build path is `PROVEN_REAL` up to the point a device is required.** An owner-style request becomes a real EXE, a real portable package and a real MSIX on this machine, each read back by something that did not build it. The EXE has **not** been launched, because launching it is M19's path through a device runtime the owner has not installed yet.

---

## THE TOOLCHAIN WAS RE-MEASURED BEFORE ANY OF IT WAS DESIGNED AGAINST

`docs/evidence/m28-toolchain-2026-09-09.json`, measured on the finished machine rather than trusted from the day-old file — the M27 discipline, where a registry key with no executable behind it was about to be reported as an installed Photoshop.

Two of the draft spec's assumptions did not survive, and **both would have shaped code**:

| assumption | measurement | consequence |
|---|---|---|
| MAUI would cover "Windows and Android together" | `dotnet workload list` reports **no MAUI workload** | MAUI is not a stack this milestone has. A both-platforms request produces two projects from one spec. |
| the PE header would be "read by `pefile`" | **`pefile` is not importable** | Adding a dependency to the Cloud Core image for one function is exactly the trade ADR-0093 refused for numpy in M27. The reader is standard library only. |

The same run brought the good news: `aapt2` **is** present (build-tools 33.0.0), and `makeappx` and `signtool` are both at Windows Kits `10.0.26100.0`.

## PROVEN_REAL: the Windows pipeline (`docs/evidence/m28-native-windows-lab-2026-09-09-091306.json`)

`scripts/tests/native-windows-lab.py` runs the owner's request through the real pipeline with no fake in the path:

| step | result |
|---|---|
| `"Bana notlarımı tutacak bir Windows masaüstü uygulaması yap."` → `NativeAppSpec` | `notlarim`, `dotnet_wpf`, `0.1.0` |
| template → project | **9 files**, every `{{SLOT}}` filled |
| M23 `ProjectFiles` policy, **unchanged** | accepted — one policy, not a laxer second one written for native |
| `dotnet build` | exit 0 |
| `dotnet test` | **5/5**, including persistence across a restart |
| `dotnet publish -r win-x64 --self-contained` | exit 0 |
| **the artefact** | **`notlarim.exe`, 162,304 bytes** |
| an INDEPENDENT reader opens it | x64, `windows_gui`, version `0.1.0`, sha256 `175164a9e06475da…` |
| verdict | **AGREES** |

Two more targets, produced and read back the same way:

- **`windows_portable`** — 59,574,644 bytes, the self-contained publish zipped.
- **`windows_msix`** — 60,592,458 bytes, built by **Windows' own `makeappx.exe`**, reading back as identity `PagentOS.notlarim`, version `0.1.0.0`, x64, display name `Notlarım`, 250 payload entries.

The build is reproducible: re-running the lab after the containment and extension guards landed produced a **byte-identical sha256**.

### The reader is independent, and was qualified against binaries it did not build

`app/nativefactory/artifacts.py` is `struct` over the DOS/NT headers plus a scan of `VS_VERSIONINFO`; `zipfile` + the hardened XML parser for MSIX; `aapt2` for APK when the SDK is there. It was pointed at Windows' own `mspaint.exe` (x64, `windows_gui`), the .NET host (`windows_console` — the distinction the validation depends on), and this repository's installed agent, **whose version string `1.0.0+a3cb04ea…` it reproduced exactly**. One test compares its answer with Windows' own version API, so a reader that agreed only with itself would fail.

## NOT signed, by decision

The MSIX carries no signature. Signing needs a certificate, and the owner's real signing identity is theirs — not something an autonomous build reaches for. The package says so about itself: publisher `CN=PagentOS Unsigned Build`, display publisher `PagentOS (imzasız)`, and the receipt tells the owner an unsigned MSIX installs only where its publisher is trusted.

## Honest classification is an output, not an apology

| platform | state | why |
|---|---|---|
| Windows EXE / portable / MSIX | **REAL** | .NET SDK 10.0.400, `makeappx`, `signtool` all present |
| Android | **`WAITING_OWNER_JDK`** | the SDK, `adb`, the emulator, build-tools 30.0.3/33.0.0 and system images are ALL here — and there is no Java. Gradle, `javac`, `apksigner` and `avdmanager` all run on it. Owner item 33. |
| iOS | **NOT BUILT** | no macOS, no Xcode, and with no MAUI not even a shared head. **Not a target value at all**, and no owner action is offered, because none exists. |

The Android sentence is written the way it is *because* the blocker is confusing precisely when the SDK is present: an owner told only "Android unavailable" would go looking for the wrong thing. The Android template refuses by name (`template_unavailable`, naming item 33) rather than shipping one whose build can only fail.

## NINE DEFECTS, every one found by running rather than reading

1. **A WPF project's implicit usings do not include `System.IO`** — the generated `NoteStore.cs` failed with eleven `CS0103` errors on the first real build. Kept as a static guard so CI catches the class without a compiler.
2. **`PublishSingleFile=true` cannot restore `Microsoft.NET.ILLink.Tasks` here** (NU1100). A self-contained folder publish produces the same EXE and is what `windows_portable` zips anyway — so the flag is dropped and the **reason** is recorded, in the lab and in the spec, rather than quietly changed.
3. **The dotnet CLI answers in Turkish on this machine**, so the lab's summary parser found nothing and wrote `None` where a test count belonged. The CLI language is now pinned, which also makes the lab readable on a CI runner.
4. **`makeappx` refused the first manifest** (error 80080204): a WPF application is a full-trust desktop program and the manifest did not declare `runFullTrust`. Fixed by declaring the capability the application actually uses — not by choosing an entry point that would misdescribe the package.
5. **A missing tool arrived as a raw `WinError`** from `subprocess`, telling the caller nothing about *what* was missing.
6. **The spec refused `&` in an application name.** "Notlar & Fikirler" is a name, not an attack. The refusal now covers markup structure only.
7. **`generate()` resolved nothing** and wrote wherever its caller pointed.
8. **`appfactory` vs `apps`** — a **released** mismatch: the Cloud Core publishes `SUBSYSTEM_APPFACTORY = "appfactory"`, and the web's mirror, its label table and a test fixture all said `"apps"`. Every `app.factory` row in the Defter printed the raw token instead of "Uygulamalar" **for five milestones, both suites green**.
9. **The panel asked for a route that answers 422, not 404** — `/v1/native/builds` against a router whose `{build_id}` is typed `uuid.UUID`. The web maps every non-404 to `failed`, so the panel would have shown "Alınamadı: HTTP 422" for ever instead of "henüz yok".

Plus one of my own, worth naming because it is the recurring kind: **a vacuous test**. My first clock-wiring test asserted a call site was *present in the source*, and a mutation wrapping it in `if False:` sailed straight through. It drives a real object and counts now.

## Security and policy

- **Containment is resolve-then-contain** — symlinks, `..` and absolute paths resolved *first*, then compared against the resolved root, and refused **before a single file is written** (a test asserts the directory does not exist afterwards, not merely that the row says failed).
- **An extension allowlist**, because a native project is compiled and RUN: MSBuild will happily execute a `.ps1` a project file points at. A named executable list sits beside the allowlist so widening one means looking at the other, and an assert holds that they can never overlap.
- **Free text never reaches code.** Every template slot comes from a validated field; a display name may appear in XAML and the `.csproj`'s metadata (escaped by those formats) and a test asserts it appears in **no `.cs` file**.
- **The REST surface is read-only**, asserted from the app's own published OpenAPI contract *and* from a 405. A verb that kicked off a twenty-minute compile from a panel refresh would be a fifth way to start work nobody was watching.
- **The assistant installs nothing and downloads nothing.** A missing toolchain is an owner item, and saying so is the feature.

## The Living Core

UI contract **v13**, additive and append-only — held by a test that freezes the v12 vocabulary as an ordered prefix, which caught the web track's own first attempt. `native.build` publishes from `_touch`, the one place every transition goes through, so the wire cannot drift from the row: the event's state is `wire_step(row.state)` computed from the row just committed. `verdict_ok` is read from the reader's verdict and **never** inferred from `state == "verified"` — both halves read it the same way on purpose.

The "Yerel Uygulamalar" panel has **no controls at all**: a build is a twenty-minute compiler plus a package install, and a button would be a second authority surface.

## What closing still needs

| | |
|---|---|
| **The EXE launched and driven** | M19 through the device runtime — **owner item 28**, the critical path. `scripts/core/qualify-item28-unlocked.ps1` runs it automatically the moment the update lands, and refuses against the old runtime rather than reporting a false success. |
| Voice intents + the `nativeapps` corpus category | in progress |
| The device build runner + allowlist | in progress |
| Stage 26, written from evidence | after the above |
| `BUILD_STATE` reconciled | after Stage 26 |

**M28 must not close on the Windows path without the launch.** The lab stops exactly where the honest boundary is and its evidence file says so.
