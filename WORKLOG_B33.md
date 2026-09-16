# WORKLOG — B33 row 473 "Signing optional/test cert" (2026-09-16 / 17)

Owner decision (2026-09-16, verbatim): **"win uygulamada da kendinden imzalı olsun"**. Windows
applications produced by the native factory are signed with a SELF-SIGNED certificate. APK
signing is out of scope (Android is paused).

Base: `main` 9acdb48. Worktree branch only; nothing pushed. `docs/product/*.md` and
`docs/DECISIONS.md` are NOT edited. The text the lead should integrate is below.

---

## 1. What was built

### Device (Session Companion, owner session only)

- `Native/OwnerSigningIdentity.cs`: the self-signed identity.
  - RSA-3072, made by `CertificateRequest` in process.
  - Subject exactly `CN=PagentOS Owner Test Signing`.
  - Extensions: basic constraints CA=false (critical), key usage DigitalSignature (critical), and EKU `1.3.6.1.5.5.7.3.3` as the only EKU.
  - Valid 2 years (NotBefore = now − 5 min).
  - The key is a persisted CNG key (Microsoft Software KSP, user key, DPAPI-protected by Windows) with `ExportPolicy = None`.
  - The certificate lives in `CurrentUser\My`, linked to that key.
  - It is created on first need and reused by the thumbprint recorded in `%LOCALAPPDATA%\PagentOS\signing\identity.json`.
  - The public certificate is exported as DER to `owner-test-signing.cer` beside it. No other certificate bytes are ever written.
  - It is replaced when missing, of the wrong shape, expired, or within 30 days of expiry. The replacement is untrusted, and every answer says so.
  - `RemoveAllForLab()` refuses to touch the owner store or prefix.
- `Native/PackageSigner.cs`: signing and read-back.
  - Signs with `SignerSignEx2` (mssign32) using `APPX_SIP_CLIENT_DATA`, SHA-256, `SIGNER_CERT_POLICY_CHAIN_NO_ROOT`, and **no timestamp**. There is no child process.
  - Read-back uses two readers, neither of which is the signer:
    - `WinVerifyTrust` (generic verify v2, no revocation, cache-only URL retrieval);
    - `AppxSignature.p7x` (`PKCX` + CMS, `CheckSignature(verifySignatureOnly)`), which names the signer.
- `Native/MsixDeployment.cs`: per-user MSIX install and removal.
  - Calls `Windows.Management.Deployment.PackageManager` over its ABI (vtable slots taken from SDK 10.0.26100 `windows.management.deployment.h`), on a dedicated MTA thread with a 5 min bound.
  - Package names come from Windows' own `PackageFullNameFromId` / `PackageFamilyNameFromId`.
  - Registration is proved by `GetPackagesByPackageFamily`.
- `Native/NativeSigning.cs`: the container (identity, deployer, signer). The signer is a lab seam, used to prove the read-back refusal. The read-back itself cannot be replaced.
- `Projects/NativeLifecycle.cs`:
  - `project.package` gains `signing_mode`:
    - `unsigned` is the default, which is what an older Cloud Core gets.
    - `test_certificate` checks the manifest Publisher first, then packs, signs, and reads back.
    - A signature that does not read back deletes the package and answers `postcondition_failed`.
    - A signer failure deletes the package and answers `dependency_unavailable` with `signing_failed: …`.
    - `owner_certificate` is `permission_denied` by name; any other value is `validation_error`.
    - A portable package is never signed; its answer carries `signing_note: portable_not_signed`.
  - `project.install` gains `kind: msix`. It refuses `package_unsigned:` and `signing_cert_untrusted:` first (`permission_denied`), then calls the deployer. An install is answered only when Windows lists the package for the user; otherwise `postcondition_failed`. The record carries `method: msix`.
  - `project.uninstall` removes an msix install and verifies that the package is gone.
- `ProtocolConstants.cs`:
  - New `NativeCapabilityNames` constants: signing modes, `TestSigningSubject`, `CodeSigningOid`, `TrustScript`, and the two refusal markers.
  - `ProjectCapabilityNames.LifecycleCommandTimeoutCap` (5 min 30 s) and `IsLongLifecycle`.
  - The `ForbiddenPrograms` list is **unchanged**; its comment was updated.
- The refusal messages in `ProjectManifest.cs` / `ProjectRunner.cs` / `NativeTools.cs` / `Program.cs` now say "runs no signing program" instead of "signs nothing". The refusal is unchanged: no manifest command signs, and no certificate tool process is ever started.

### Service

- **Bug found and fixed:** `project.package`, `project.install` and `project.uninstall` rode the 30 s projects cap.
  - Meanwhile the companion allows makeappx 5 min, and the Cloud Core waits 330 s (`PACKAGE_TIMEOUT_S`). A cold pack of a 60 MB publish folder would have been answered `timeout` by the service while still being written.
  - `InteractiveCapabilityExecutor.TimeoutCapFor` now routes these three to `ProjectLifecycleTimeoutCap` (5 min 30 s).
  - Regression test: `NativeBoundsTests.The_packaging_and_install_ceiling_covers_…`. It also reads `PACKAGE_TIMEOUT_S` / `MSIX_INSTALL_TIMEOUT_S` from `device_lifecycle.py`.

### Cloud Core

- `app/nativefactory/signing.py`:
  - `test_certificate` is applied, and is the **default** (`DEFAULT_MODE`, and `config.native_signing_mode = "test_certificate"`).
  - `unsigned` is an explicit opt-out. `owner_certificate` stays refused by name and is never forwarded to the device as anything but `unsigned`.
  - New pieces: `TEST_SIGNING_SUBJECT`, `TRUST_SCRIPT`, `TRUST_COMMAND`, the device markers, `install_refusal()`, and new error classes (`signing_cert_untrusted`, `package_unsigned`, `signing_mode_refused`).
  - `speech_for(kind, signed=, trusted=)` speaks the DEVICE's answer, never the policy's intention.
- `packaging.appx_manifest_text(spec, signed=)`: Publisher = the signing subject when signed. The display name is "PagentOS (kendinden imzalı)".
- `device_build.build_on_device(..., signing=)`:
  - Scaffolds the Publisher and sends `signing_mode`.
  - `signature_facts()` copies the signer and trust only when the device said `signed: true`.
- `device_lifecycle`:
  - `package_on_device(signing_mode=)`.
  - `install_kind_for(row)`: an msix row installs with `kind: msix`, bound 330 s, proven by `observed.package_registered`; a shortcut is not accepted as proof.
  - Uninstall is unverified while the package is still registered.
- `tools_native`:
  - `native.package` speaks and stores the device's signature facts. `owner_action` carries `{script, command}` while untrusted.
  - `native.install` / `native.update` apply the MSIX gates, map the device's refusals by first word, and name the trust script. The success speech differs for msix.
  - `native.uninstall` says "paket kaydı gitti" for msix.

### Owner step

- `scripts/trust-native-signing-cert.ps1` (elevated, owner-run, PS 5.1) and `scripts/lib/NativeSigningTrust.ps1`.
  - The script imports ONLY `owner-test-signing.cer` into `LocalMachine\TrustedPeople`. The target is fixed and is not a parameter; the script never names Root.
  - Before importing, it checks that the certificate:
    - carries no private key;
    - is the recorded (or given) thumbprint;
    - has the exact subject;
    - is self-issued with a valid self-signature;
    - is an end entity whose only EKU is code signing;
    - is currently valid;
    - is present WITH its key in the owner's `CurrentUser\My`.
  - It is idempotent. `-Remove` undoes one thumbprint and refuses a certificate with a different subject.
  - Unelevated, it exits 2 before touching anything. It never elevates itself and runs no certificate tool.
- `scripts/tests/native-signing-trust.tests.ps1` has 54 checks. They run against throwaway CURRENT-USER stores (removed afterwards; the key-file count is checked). The suite is wired into `scripts/quality-gate.ps1` and `.github/workflows/ci.yml`. On an elevated runner the real script is not run; the unelevated-refusal case is skipped there.

### Protocol

- `packages/protocol/DEVICE_PROTOCOL.md`:
  - §6l: the caps sentence and the `project.package` / `install` / `uninstall` rows.
  - §6n: the "runs no signer / signs in process" section, plus "Signing", "The owner's one step" and "Install".
  - The lab paragraph and the Session Companion summary.
- No bundled contract file changed (`sync-protocol-bundle.py --check`: clean).
- **No capability name was added**, so the manifest counts are unchanged. The change is additive: an older Cloud Core sends no `signing_mode` and gets `unsigned`; an older device ignores it, answers `signed: false`, and the owner hears "imzalanmadı".

---

## 2. Proposed matrix rows (lead integrates; 14 columns, 15 pipes each, no pipe inside a cell)

```
| 457 | MSIX | `staging/AppxManifest.xml` Cloud Core'dan iskelelenir (Publisher imza politikasına göre: `CN=PagentOS Owner Test Signing` / imzasız yayıncı), cihaz `makeappx pack /o /nv` ile paketler; `signing_mode: test_certificate` ile companion kendi sürecinde imzalar ve geri okur; makeappx yoksa `dependency_unavailable`; `native.package` cihazın okuduğu imza durumunu söyler | Erişilebilir | DONE | PA | P1 | 472 | B33 | NativeLifecycle.cs:Package(msix); packaging.py:appx_manifest_text(signed); device_build.py:signature_facts; tools_native.py:_package_on_device | test_voice_native_tools.py (msix cihazda imzalı), test_nativefactory_device_build.py (Publisher, imza gerçekleri), C# NativeLifecycleTests + NativePackageSigningTests (gerçek makeappx) | lab: makeappx + SignerSignEx2 + WinVerifyTrust bu masaüstünde (0x800B0109) | no | imza programı hiçbir yerde koşmaz: ForbiddenPrograms duruyor |
| 472 | Signing policy | `native_signing_mode` (varsayılan `test_certificate`, sahip kararı 2026-09-16; `unsigned` açık vazgeçiş; `owner_certificate` ADLA reddedilir ve cihaza asla iletilmez); her paket cevabı CİHAZIN geri okuduğu imza/güven durumunu söyler; imzasız MSIX'in kurulumu `package_unsigned` ile reddedilir | Politika | DONE | PA | P1 | — | B33 | app/nativefactory/signing.py; config.py:native_signing_mode; tools_native.py:_msix_install_refusal,_install_failure | test_nativefactory_lifecycle.py (politika 5), test_voice_native_tools.py (imza 6), test_nativefactory_signing_contract.py (C# kaynağını okur) | — | no | özel anahtar cihazdan çıkmaz; zaman damgası yok (ağ yok) |
| 473 | Signing optional/test cert | Companion'ın kendinden imzalı kod imzalama kimliği (RSA-3072, `CN=PagentOS Owner Test Signing`, EKU yalnız kod imzalama, CA=false, 2 yıl, CurrentUser\My, CNG anahtarı DIŞA AKTARILAMAZ, parmak iziyle yeniden kullanılır, bitişe 30 gün kala yenilenir); MSIX süreç içinde `SignerSignEx2` ile imzalanır, `WinVerifyTrust` + `AppxSignature.p7x` ile geri okunur, okunamazsa paket silinir; `project.install kind=msix` güvenilmeyen imzacıyı `signing_cert_untrusted` ile reddeder ve betiği adlandırır, güvenilirse Windows PackageManager ile kullanıcıya kurar ve kaydı okur; `scripts/trust-native-signing-cert.ps1` yalnız genel sertifikayı LocalMachine\TrustedPeople'a alır | Test sertifikası | DONE | PA | P1 | 472 | B33 | Native/OwnerSigningIdentity.cs; Native/PackageSigner.cs; Native/MsixDeployment.cs; NativeLifecycle.cs:SignAndReadBack,InstallMsix; scripts/trust-native-signing-cert.ps1; scripts/lib/NativeSigningTrust.ps1 | NativeSigningIdentityTests (9), NativePackageSigningTests (8), NativeMsixInstallTests (8), NativeSigningTests (+2), native-signing-trust.tests.ps1 (54), mutasyon M1-M8 | lab gerçek: imza 0x800B0109, kurcalama 0x80096010, Windows PackageManager güvenilmeyen paketi kendisi reddetti (0x800B0109), olmayan paket kaldırma 0x80073CF1 | güven adımı (READY_FOR_OWNER): yönetici PowerShell'de bir kez `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\trust-native-signing-cert.ps1`; önce yeni cihaz ajanının dağıtımı ve bir MSIX paketlemesi | güvenilir kurulumun başarılı yolu yalnız sahibin adımından sonra gerçek makinede kanıtlanabilir; taşınabilir EXE Authenticode kapsam dışı |
```

Row 468 (optional note to append): "MSIX satırı `kind=msix` ile kullanıcıya kurulur (473); kanıt `observed.package_registered`".

---

## 3. ADR draft — "ADR-XXXX (B33 signing)"

**ADR-XXXX — The factory's Windows packages are self-signed on the device, and trusting them is one owner step (2026-09-16, B33 req 473)**

Status: Accepted. Owner decision 2026-09-16, "win uygulamada da kendinden imzalı olsun". It supersedes the "signs nothing" half of ADR-0095 decision 4 and addendum 2 §5. The "runs no signer" half stands.

**Context.** Until now every MSIX was unsigned, and `test_certificate` was a named-and-refused mode. An unsigned MSIX never installs, so the MSIX target was a file nobody could install.

**Decision 1: the default flips to `test_certificate`.**
- The owner decided, and the setting still overrides.
- `unsigned` stays available as an explicit opt-out.
- `owner_certificate` stays refused by name. The Cloud Core never forwards it as anything but `unsigned`.

**Decision 2: the identity is the Session Companion's, in the owner's profile.**
- It is a self-signed RSA-3072 code-signing certificate created by `CertificateRequest`. No certificate tool is run.
- The key is a persisted CNG user key with export policy None; Windows' key storage protects it with DPAPI.
- The certificate is in `CurrentUser\My`, identified by the thumbprint in `identity.json`.
- The public DER is exported for the trust step. Nothing else is written, logged or sent.
- The subject is fixed, because the MSIX signer requires Publisher == subject byte for byte. The Cloud Core scaffolds that Publisher.
- Validity is 2 years, with renewal 30 days early. The renewal is honest: the new certificate is untrusted, and the answers and the log say so.
- ECDSA was not tried, so it is not claimed.

**Decision 3: signing is in process and untimestamped.**
- The signer is `SignerSignEx2` with the package SIP. `ForbiddenPrograms` is unchanged, and no manifest command can sign.
- There is no timestamp, because that would be a third-party network call on the owner's behalf.
- Consequence: a package stops verifying when its certificate expires. That is why renewal is early.

**Decision 4: a signature is only what an independent reader found.**
- Two readers check it: `WinVerifyTrust`, and the package's own CMS block, which names the signer.
- `signed: true` needs an intact status plus the identity's thumbprint. Otherwise the package is deleted and the answer is `postcondition_failed`.
- Measured on this machine: intact and untrusted = `0x800B0109`; one byte tampered = `0x80096010`; unsigned = `0x800B0100`. Re-zipping after signing gave `0x800B0003`, so the tamper test flips a byte in place.

**Decision 5: trust is the owner's one elevated step, and the device never elevates.**
- `trusted` = the verifier said success, or the thumbprint is in `LocalMachine\TrustedPeople` or `Root`. Those stores are opened read-only.
- `scripts/trust-native-signing-cert.ps1` writes only `LocalMachine\TrustedPeople`, and only the companion's public certificate, after seven checks.
- Its tests run against throwaway current-user stores.

**Decision 6: an MSIX row installs per user through Windows' own PackageManager ABI.**
- There is no shell and no cmdlet.
- The trust gate runs first; Windows' own refusal is the backstop, and a lab test proves it.
- "Installed" means `GetPackagesByPackageFamily` lists the package.
- The device's two refusals are recognised by their first word, because the wire error has no detail field.

**Decision 7: the portable zip is not signed.**
- Its EXE was already read back and hashed, and signing it afterwards would falsify that hash.
- A self-signed Authenticode signature would not satisfy SmartScreen either.

**Decision 8: no capability name is added.**
- The manifest counts are unchanged (40 / 89 with `-Operator`).
- The protocol change is additive in both directions.

**Found on the way (fixed, with a regression test).** The service capped `project.package`, `project.install` and `project.uninstall` at 30 s, while the companion allows 5 min and the Cloud Core waits 330 s. They now have a 5 min 30 s cap, and a C# test reads the Cloud Core's two waits from its source.

**Consequences.** The factory's MSIX is signed, and it is installable after one owner step. Every receipt speaks the device's read-back. The successful trusted-install path is proved only up to Windows' own refusal on this machine; its success needs the owner's step (READY_FOR_OWNER).

---

## 4. Evidence notes (measured on this desktop, 2026-09-16/17)

- **makeappx + SignerSignEx2 + WinVerifyTrust, real.**
  - The signed lab package read back as `0x800B0109` (CERT_E_UNTRUSTEDROOT). The block named the lab identity's thumbprint.
  - A one-byte in-place tamper read back as `0x80096010`.
  - An unsigned package read back as `0x800B0100`.
- **Windows PackageManager, real, on the untrusted signed package:** `msix_install_failed (0x800B0109): error 0x800B0109: The root certificate of the signature in the app package or bundle must be trusted.` Nothing was registered.
- **RemovePackageAsync on a non-existent full name:** `0x80073CF1`, with Windows' "…current user does not have that package installed" text.
- **Package naming check:** Windows names the Calculator identity `Microsoft.WindowsCalculator_8wekyb3d8bbwe` / `…_10.2103.8.0_x64__8wekyb3d8bbwe`, which is the known family.
- **Cleanup verified after the C# lab:**
  - No `HKCU\Software\Microsoft\SystemCertificates\PagentOS*` store remains.
  - No PagentOS certificate is in `CurrentUser\My`.
  - No new key file is in `%APPDATA%\Microsoft\Crypto\Keys` (all 7 files predate today).
- **Cleanup verified after the PS lab:** both stores are gone, and the key-file count is 20 before and 20 after.
- **The owner's store was never written by any test:**
  - The C# lab uses its own `PagentOSLab-<run>` store.
  - The PS lab uses `PagentOSTrustLab{Owner,Target}-<run>`.
  - No LocalMachine store was opened for writing.

---

## 5. Test counts

| suite | before (9acdb48) | after |
|---|---|---|
| C# `PagentOS.Agent.Tests.Native` | 108 (107 passed, 1 skipped) | 136 (135 passed, 1 skipped: the Android Gradle lab) |
| C# full `PagentOS.Agent.Tests` | not measured at base | 1033: 1031 passed, 1 skipped, 1 failed (see §7) |
| C# `PagentOS.Companion.Audio.Tests` | — | 135 passed |
| Python `test_nativefactory_lifecycle.py` | 22 | 27 |
| Python `test_nativefactory_device_build.py` | 30 | 32 |
| Python `test_nativefactory_packaging.py` | 9 | 10 |
| Python `test_voice_native_tools.py` | 31 | 36 |
| Python `test_nativefactory_signing_contract.py` (new) | 0 | 3 |
| Python final subset (7 files) | — | 156 passed |
| Python broad subset (25 files, incl. corpus, appfactory, step-up) | — | 2881 passed, 2 skipped (run before the last 4 tests were added) |
| PS 5.1 `native-signing-trust.tests.ps1` (new) | 0 | 54 passed |
| PS 5.1 `script-syntax` / `harness-symbols` | — | 111 scripts OK / 145 passed |

Also checked:
- `dotnet build` of the solution: 0 warnings, 0 errors. Warnings are errors in this repo.
- `dotnet format --verify-no-changes`: clean on my files.
- `ruff check`: clean.
- `ruff format`: clean on my files.

---

## 6. Mutation RED proofs

Each mutation file was copied to a backup with its sha256 recorded, then mutated. After the test run the backup was copied back and the sha256 verified (never `git checkout --`). A C# test was only run after the mutated build succeeded.

| id | file | mutation | RED tests | restored sha256 |
|---|---|---|---|---|
| M1 | Native/PackageSigner.cs | `IsIntact` also accepts `TRUST_E_BAD_DIGEST` | `A_package_changed_after_signing_does_not_read_back_as_signed`, `A_signature_that_does_not_read_back_is_never_answered_as_signed_and_the_package_is_removed` | e0b49d14…5c96b54b |
| M2 | Projects/NativeLifecycle.cs | untrusted-signer gate disabled (`&& false`) | `An_untrusted_signer_is_refused_by_name_with_the_owner_s_trust_step_and_windows_is_never_asked` | 4af77636…25f983f |
| M3 | Native/OwnerSigningIdentity.cs | `ExportPolicy = AllowPlaintextExport \| AllowExport` (the key is exportable) | 15 red, incl. `The_private_key_is_persisted_…_cannot_be_exported_in_any_form`, `A_second_need_reuses_…`, renewal | 03ab4733…efd69a6e |
| M4 | DeviceService/InteractiveCapabilityExecutor.cs | lifecycle cap branch disabled | `The_packaging_and_install_ceiling_covers_…` | 43368bd1…738dcdf2 |
| M5 | app/nativefactory/device_build.py | signer facts copied even when unsigned | `test_an_msix_row_is_built_then_packaged_…`, `test_a_signed_package_carries_the_signer_and_trust_…` | e8e0e7cb…d11239d |
| M6 | app/nativefactory/device_lifecycle.py | msix install proven by `shortcut_exists` | `test_an_msix_row_installs_its_package_and_only_a_registration_…`, `test_after_the_trust_step_the_signed_msix_installs_…` | 2a93c21c…1dabdcd8 |
| M7 | app/nativefactory/signing.py | signed MSIX always spoken as trusted | `test_what_the_owner_hears_is_the_device_s_answer_…`, `test_packaging_an_msix_on_the_device_speaks_the_signature_…` | 8a5d5a4e…2813ba1b7 |
| M8 | Projects/NativeLifecycle.cs | post-sign read-back gate never taken (`readBack.VerifyStatus == int.MinValue`) | `A_signature_that_does_not_read_back_is_never_answered_as_signed_and_the_package_is_removed` | 4af77636…25f983f |

In the table above, `\|` inside M3 is a literal C# `|`, not a cell separator. Two earlier M8 attempts (`if (false)`, `readBack is null`) failed to COMPILE (CS0162 / CS8602 as errors). Their test runs used a stale binary and are **not counted**.

---

## 7. Found, not mine, not fixed

- **`SceneUnityTests.The_real_unity_editor_answers_either_the_licence_refusal_or_the_run_and_its_inspection`** fails deterministically on this desktop: the real Unity run's `exit_code` is 1, where the test expects 0.
  - Nothing on that path touches signing.
  - The main checkout has uncommitted B50 changes to `SceneUnityTests.cs` / `SceneLab.cs` / `SceneDriver.cs`, which is where this belongs.
- **`dotnet format` rewrites `Documents/LegacyOfficeExtractor.cs`** (the BIFF8 function table) on base 9acdb48. I reverted that rewrite and did not include it. The gate's `--verify-no-changes` may flag it on main.
- **ruff 0.16.5 (the venv's) would reformat** `nativefactory/{artifacts,interrupted,roots,service,spec,stacks}.py` and one `tools_native.py` statement on base. I reverted all of those to base formatting.
- **The Cloud Core's `ToolchainFacts` still measures a `signtool` path** (`stacks.py`). It is informational only and nothing runs it. I left it alone.

## 8. Not done / READY_FOR_OWNER

1. **Deploy the new device agent to the live device.** This is the existing owner release item. The installed runtime is not this build.
2. **Package one MSIX through the factory.** This creates the identity and `owner-test-signing.cer`.
3. **Run the trust step once, from an ELEVATED Windows PowerShell opened from the owner's own account, in the repo root:**
   `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\trust-native-signing-cert.ps1`
4. **Then run "Uygulamayı kur" for the MSIX row.** This is the first real success of the trusted-install path. No test here can prove that path without writing LocalMachine.
5. **Portable-EXE Authenticode is out of scope** (see ADR decision 7). ECDSA was not tried.
6. **Web panel not touched.** The row carries the signer and trust (`artifact_json.package.signed / signer_thumbprint / trusted`). A grep of `apps/web/app/**/native*` finds no reader of those keys, so showing them in "Yerel Uygulamalar" is a web follow-up.
