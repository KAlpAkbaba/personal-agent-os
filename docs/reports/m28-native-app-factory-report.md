# M28 Native Application Factory — milestone report (2026-09-11, IN PROGRESS)

> Takeover correction, 2026-09-11: owner item 28 and row 26.15 are already
> `PROVEN_REAL` in Qualification Stage 28 (`item28-unlocked-20260909-190753.json`):
> the EXE was launched, driven through UI Automation, closed, relaunched with persisted
> state, and its own log was read. M28 remains open for row 26.16, the missing production
> `native_runner` that triggers the device build lifecycle. The older narrative below is
> retained as the history of the pre-install state.

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
| ~~Voice intents + the `nativeapps` corpus category~~ | **done.** 1813 cases across 21 categories, `nativeapps` 94, every earlier category unchanged case for case; 78 voice tests across two files. Stage 26 rows 26.10-26.12. |
| The device build runner + allowlist | the DEVICE half is done and in `packages/protocol/DEVICE_PROTOCOL.md` §6n: the four `dotnet`/`makeappx` shapes as bounded batch children under the `native` root, matched token for token, nothing signed, no new capability name. What is missing is the CLOUD CORE side - see the row below. |
| ~~`app.launch` cannot reach a built application~~ — **RESOLVED, and it was my error** | `app.launch` allowlists a name or a path under Program Files / Windows, so it genuinely cannot start a freshly built application — that part was right. Concluding that no path exists was not. `file.open` takes its path through `RequireAuthorisedPath` (resolve-then-contain against the owner's authorised roots) and, with no `application`, runs it with ShellExecute, answering with `pid`, `window_id` and the window it observed. The native root **is** an authorised root — the M28 device work added it explicitly. So no device change is needed, and this is the NARROWER grant: the file must already be inside a root the owner authorised, rather than widening `app.launch`'s allowlist to cover the whole Projects tree. The qualification launches and relaunches through `file.open`, tolerating a slow cold start by asking `window.list` for the pid. |
| The lifecycle as an M26 executive graph (spec §4) | **not built.** `nativefactory.*` step kinds do not exist. Worth stating plainly rather than leaving the spec's sentence to imply otherwise — and worth noting WHY it is not merely an oversight: the Cloud Core runs on Linux, so `dotnet build` for a WPF application cannot execute there at all. The step handler must dispatch to the device, which is the same thing item 28 gates. |
| **The Cloud Core cannot yet TRIGGER a build on the device** | The voice family, the rows, the channel and the panel are all real, and the device half is real - the four `dotnet`/`makeappx` shapes ride the existing `project.scaffold(root:"native")` + `project.run`, with no new capability names (DEVICE_PROTOCOL 6n). What does not exist is the Cloud Core object that drives them: nothing supplies `native_runner`/`native_root` outside the corpus harness, so on production `native.build` answers `dependency_unavailable` and says so. That is honest, not silent - but it means the build is provable on this machine through the lab and NOT startable from production. Wiring it is a real piece of work and it cannot be exercised until item 28 lands either, because the deployed agent advertises no `project.*` at all. **The shape of the missing piece is now known exactly, read out of the two halves that already exist rather than designed in the abstract.** `generate()` splits cleanly at the point it writes: `render(spec)` produces the file set, `check_extensions` and `validate_files` accept it, and only then is it written to a local directory. The device wants the same file set through `project.scaffold {root: "native", files: [{path, text}], manifest}` (=< 200 files, =< 2 MiB, text only) and answers with `root_path` - which IS the production `native_root`. `BuildRunner` is already a Protocol of one method, `run(argv, cwd, *, timeout_s) -> RunResult`, written that way so "the Cloud Core's device-dispatched runner, the lab's subprocess and a test's fake are the same thing": the device-dispatched one issues `project.run` with the argv the lifecycle built and maps the terminal outcome onto an exit code and its output. `runtime.register_live()` is how `create_app` already injects process-wide runtimes into every `ToolContext.live`, which is where `native_runner` and `native_root` are read. So this is an integration, not a design - and it is still gated on item 28 for its PROOF, because the deployed agent advertises no `project.*` at all. |
| ~~Stage 26, written from evidence~~ | **done** (`docs/QUALIFICATION.md`, 17 rows), and Stage 27 now records the 2026-09-09 install incident beside it. |
| ~~`BUILD_STATE` reconciled~~ | **done**, and it says `M28_IN_PROGRESS_AWAITING_ITEM_28` rather than closed. |
| ~~**CI green**~~ | **DONE — and this row was WRONG for as long as it stood after the billing was resolved.** It read *"externally blocked … GitHub Actions has refused to START any job"* while CI had been running and green for hours: run `34341441268` on `7527877`, then `34349780427` on `53f338b` and `34351626583` on `bd23f47`, all seven jobs each, the Windows job carrying 838 agent tests, the clean-process regression (20) and `STAGED UPDATE QUALIFIED` (85 checks) inside it. A record that keeps saying "blocked" after the block lifts is the same defect as a test that keeps passing after the thing it tests breaks. |

**M28 must not close on the Windows path without the launch.** The lab stops exactly where the honest boundary is and its evidence file says so.

## 2026-09-09 — the owner's item-28 install failed, and it was the installer's fault

The one thing M28 still needs is the elevated install (row 26.15). The owner ran it. It
rolled back, and the record belongs here because it is what stands between M28 and closing.

`docs/evidence/item28-owner-install-2026-09-09-140405.json` holds the run; `ADR-0097` holds
the reasoning; Stage 27 of `docs/QUALIFICATION.md` holds what is now proven.

**What worked, on the owner's real machine.** The candidate was published, staged, described
file by file and re-verified unchanged. All three trees were swapped by the journaled engine.
The candidate came up **live** as `device-service 0.6.0` — binary stamped 0.6.0, capability
manifest `5cc3d9fbd9f7`, **85 capabilities** — and the live browser worker was proven from
the companion's own audit (pid 15804, worker 0.5.0). Everything M28 needs from the runtime
was, for ninety seconds, actually running.

**What failed.** The installer's Cloud Core health gate, after 90.6 s, having sent **no HTTP
request at all**: `New-CoreDeviceFetcher` built a closure that called `Invoke-JsonUtf8`, and a
closure's module is linked to the global session state, where a function dot-sourced into the
installer's script scope does not live. It resolves when a script is started with `-File` and
not when it is started by name — and the owner starts it by name, while every harness in this
repository uses `-File`. Thirty-one identical errors, three seconds apart, and a rollback.

**What the failure did prove.** The staged update's safety property, for real: a candidate
that fails verification for *any* reason — the verifier's own defect included — leaves the
owner on a working agent. All three trees restored, the previous release judged by its own
baseline predicate and reported healthy, 96.6 s from candidate start to a working machine.

**Cloud Core was investigated separately, is not at fault, and had in fact already seen the
candidate.** Its durable record settles it: `device_sessions` row `ef480d48-…` has the
candidate connecting at 11:04:45.942853Z announcing `software_version 0.6.0` with **85
distinct capability names**, and holding that connection for **94.0 s** — the entire window
in which the installer was failing to ask it anything. The same handshake overwrites the
device row's version and capability list, so the gate the installer could not run **would
have passed**. The device row is also complete and correct against production — online, `software_version` at the top level and under
`health`, a capability list — and `scripts/core/verify-core-device-row.ps1` now proves, in
the owner's own invocation form, that the real fetcher reads the real row and that its
capability set is *exactly* what the installed binary advertises. What the installer said
about Cloud Core during the failure was the absence of a reading, not a reading of absence,
and it no longer says it.

**So row 26.15 is unchanged**: `NOT_YET_PROVEN`, and M28 does not close. What changed is that
the step which failed is now built and exercised before the owner is asked for anything — by
a regression that fails against the old line, and by gate 8 of the staged-update
qualification, which went from 71 checks to 85.
