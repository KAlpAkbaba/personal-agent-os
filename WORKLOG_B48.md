# WORKLOG — B48 "Varlık derinliği ve kamera" (device camera), 2026-09-16

Owner decision (2026-09-16, final): a PERIODIC device-local presence check through the
camera, plus an OPTIONAL CONTINUOUS mode the owner turns on. Capture only in the Session
Companion, frames processed in memory only, only derived signals leave the device, a visible
indicator, owner on/off switches, honest blocked/closed reporting, on-device no-account
method (Windows.Media.Capture + Windows.Media.FaceAnalysis).

This file carries what this branch may not write itself: the proposed matrix rows, the
proposed ADR, the proposed evidence record and the owner checkpoint. The coordinator copies
them into `docs/product/PERSONALAGENTOS_V1_FEATURE_MATRIX.md`, `docs/DECISIONS.md` and
`docs/evidence/` at merge.

## 1. What was built

### Device (devices/windows-agent)

- `PagentOS.SessionCompanion` target framework is now `net10.0-windows10.0.19041.0` (the
  WinRT projection). Its build output stays in `bin\<cfg>\net10.0-windows\` (explicit
  `OutputPath`), so no script path changed. The two test projects that reference it carry
  the same TFM (NuGet refuses a lower-versioned referrer). The companion output grows by
  ~25 MB (`Microsoft.Windows.SDK.NET.dll` + `WinRT.Runtime.dll`). Restore needs nuget.org
  (`Microsoft.Windows.SDK.NET.Ref 10.0.19041.57`), which CI has.
- `Camera/CameraContract.cs` — modes (`off|periodic|continuous`), states
  (`off|idle|capturing|blocked|unavailable|busy|vetoed|error`), indicator states, the
  in-memory `CameraFrame` (80×60 luma + face boxes, zeroed on dispose), the seams
  (`ICameraFrameSource`, `ICameraSession`, `ICameraIndicator`, `ICameraConsent`,
  `IMediaActivityProbe`).
- `Camera/PresenceClassifier.cs` — `CameraOptions`, `SampleSummary` (face fraction, mean
  luma, inter-frame motion), `CameraObservation` (exactly the seven §2 fields) and the pure
  classifier: present = face in ≥ 40 % of frames; dark scene → absent at confidence 0.2
  (fuses to UNKNOWN); resting only after 5 min present + still + no input + no sound.
- `Camera/CameraPresenceMonitor.cs` — the provider: mode state machine, periodic
  (open → 5 frames → close) and continuous (kept open) sampling, consent read before every
  open, owner tray veto, device-local kill switch (`CameraEnabled=false`), audit rows, the
  heartbeat `camera` / `presence` objects, `desktop.camera_mode` handler.
- `Camera/WindowsCamera.cs` — `WindowsFaceAnalyzer` (FaceDetector on a Gray8 SoftwareBitmap,
  box-average down-sample, buffers zeroed) and `WindowsCameraFrameSource` (MediaFrameReader,
  shared read-only first, HRESULT → blocked/unavailable/busy/error).
- `Camera/WindowsCameraSurroundings.cs` — `WindowsCameraConsent` (reads the three
  ConsentStore `webcam` values), `RenderPeakMediaProbe` (default render endpoint peak meter,
  read-only), `TrayCameraIndicator` (STA NotifyIcon: shield = armed, warning = open; menu
  "Kamerayı bu cihazda kapat" / "Kameraya yeniden izin ver").
- `AgentCapabilities.DesktopCameraMode = "desktop.camera_mode"`, appended to the always-on
  `Ambient` group. `HeartbeatStatus.Fields` += `camera`, `presence`; the Session-0 service
  projects both nested objects onto their exact keys and drops a nested object holding
  anything but short scalars.
- `ActivityStatusReporter` / `CompanionRuntime` / `Program` wired; the camera loop starts with
  the companion and closes the camera first on exit.
- Row 320: `MonitorGeometry.Power`, `IMonitorInventory.ListWithPower()`,
  `Win32MonitorInventory` DDC/CI read of VCP 0xD6 (read only); `desktop.display_status`
  `{"probe_power": true}` adds per-monitor `power` plus `monitors_powered_on` /
  `monitors_power_unknown`. The display family's structural test now also forbids
  `SetVCPFeature`, `SaveCurrentSettings`, `SetMonitor`.
- `packages/schemas/device-protocol.schema.json` (`deviceStatus.camera`, `.presence`) and
  `packages/protocol/DEVICE_PROTOCOL.md` §6o (+ §6e, §6g, §6n, §9 touch-ups).

### Cloud Core (services/api)

- `app/devices/status.py` — normalised `camera` block; `presence` kept as flat scalars;
  `StatusChange.new_camera_observation` (each reading handed out once per device by
  `observed_at`; a stamp > 5 min in the future is ignored); `as_dict()` adds `camera` and a
  boundary-screened `camera_observation`.
- `app/ambient/ingest.py` — (a') a new camera reading goes through
  `presence_service.ingest_observation` (the §2 boundary) only when `source == "camera"` and
  the owner's desired mode is not `off`; (e) `app.ambient.camera.reconcile`.
- `app/ambient/camera.py` (new) — desired mode = `ambient_policy.camera_mode` while the Active
  Eye is enabled, else `off`; `desktop.camera_mode` sent as a durable command row when the
  device's reported mode differs (≤ once a minute per device, never against a `vetoed`
  camera unless the owner wants `off`); `push_now` after a mode or eye change.
- `ambient_policy.camera_mode` (`alembic/versions/20260916_0059_ambient_camera_mode.py`,
  server default `off`), `AmbientPolicy.camera_mode`, `PUT /v1/ambient/policy
  {camera_mode}` (Literal-validated; the service refuses an unknown value too).
- `app/presence/eye.py` — every real eye flag change relays to the device cameras.
- `app/routines/dispatch.py` — `CAPABILITY_DESKTOP_CAMERA_MODE`.

### Web (apps/web)

- `lib/cockpit/api.ts` — `CameraMode`, `CAMERA_MODES`, `AmbientPolicy.camera_mode`,
  `updateAmbientCameraMode` (same owner-gated PUT), `DeviceStatus.camera`.
- `core/panels/CockpitPanels.tsx` — "Cihaz kamerası" row with the three mode chips (only
  where `onCameraMode` is given), the in-memory/no-store sentence, and each device's camera
  report (`deviceCameraText`: open / idle / off / blocked with the switch named / missing /
  busy / vetoed / error).
- Wired on `/core/cockpit` and `/settings` (named callback, `always` last — the family-page
  pins still hold).

### Scripts

- `scripts/core/qualify-device-camera.ps1` (OWNER_REQUIRED, UTF-8 BOM, CRLF) — 12 gates,
  `-DryRun`, restores the owner's policy and eye state in `finally`.
- `scripts/tests/device-camera-qualification.tests.ps1` (22 checks, added to
  `.github/workflows/ci.yml`'s PowerShell step).
- `scripts/qualify-staged-update.ps1` expects `desktop.camera_mode`;
  `scripts/core/qualify-item28-unlocked.ps1` reads the mode (never opens the camera) and
  names the new owner harness.

## 2. Manifest arithmetic (for the coordinator — the voice branch also moves it)

`desktop.camera_mode` is ONE name appended at the end of `AgentCapabilities.Ambient`
(after `desktop.notify`). Measured from the built `PagentOS.DeviceService.exe capabilities`:

| flags | before B48 | after B48 |
|---|---|---|
| none | 11 | 12 |
| `-DisplayPower` | 12 | 13 |
| `-DisplayPower` + browser worker | 42 | 43 |
| + `-Operator` | 102 | 103 |

Places that pin it, all updated: `BrowserDispatchTests` (literal list),
`test_desktop_capability_mirror.py` (`CANONICAL_DESKTOP`), `qualify-staged-update.ps1`
(`$expectedDesktop`; the count is a floor + relationships, unchanged),
`qualify-item28-unlocked.ps1` (floor 40, unchanged), `DEVICE_PROTOCOL.md` §6n/§6o. The
capability fingerprint changes (it is derived). Merge note: if the voice branch also appends
to `Ambient`, keep both names and update the literal list in `BrowserDispatchTests` and
`CANONICAL_DESKTOP` with both; every other pin is relational.

Migration merge note: `0059_ambient_camera_mode` chains from `0058_calendar_recurrence`; a
parallel branch with its own 0059 must be re-pointed (one head).

## 3. Proposed matrix rows (14 columns, 15 pipes each)

| 300 | Camera open | Cihazda (Session Companion) kamera yalnız sahibin seçtiği kipte açılır: periyodik (60 sn'de bir 5 kare) veya sürekli; kip her başlangıçta kapalı, Göz kapalıyken kapalı; açılmadan önce Windows izni okunur | Cihazda da | PARTIAL | PA | P2 | 327 | B48 | devices/windows-agent/src/PagentOS.SessionCompanion/Camera/; app/ambient/camera.py | CameraPresenceTests (29) · CameraWiringTests (14) · test_camera_b48.py (20) | gerçek kamera açılışı: qualify-device-camera.ps1 G2-G4 | kamera kararı verildi; fiziksel değerlendirme sahibin | B48: kod tam, sahte karelerle kanıtlı; gerçek MediaCapture yolu hiç kamera açmadı - OWNER_REQUIRED. Güvenlik incelemesi: sahibin tepsi vetosu sahibin profilinde (camera-veto.json) saklanır, companion yeniden başlayınca ilk kalp atışından itibaren 'vetoed' bildirir ve kapalı dışındaki her kipi permission_denied ile reddeder; Cloud vetolu cihaza kapalı dışında kip göndermez |
| 303 | Camera privacy state | Tarayıcı sekmesi ile sunucu durumu ayrı gösterilir; B48: her cihazın kamera durumu (açık / kapalı / engelli + anahtar adı / yok / meşgul / sahip kapattı / hata) panelde yazılır | Görünür durum | DONE | PA | P2 | 300 | B48 | apps/web/app/core/EyeControlView.tsx; core/panels/CockpitPanels.tsx:deviceCameraText | b48-web.test.tsx · b48-camera-web.test.tsx | tarayıcıda görsel doğrulama | no | — |
| 307 | RESTING | Cihaz kamerası hareketsiz ve mevcut sahibi 5 dk girişsiz ve sessiz görünce duruş 'resting' bildirir; füzyon RESTING'e geçer | Erişilebilir | DONE | PA | P2 | 327 | B48 | Camera/PresenceClassifier.cs; app/ambient/ingest.py | CameraPresenceTests (resting eşiği, film, giriş) · test_camera_b48.py (gece kalp atışlarıyla RESTING) | qualify-device-camera.ps1 G11 | no | Girdi boşluğu tek başına asla dinlenme değil (ADR-0155 k1) |
| 308 | LIKELY_ASLEEP | RESTING sahibin sessiz saatine göre 20 dk (dışında 30 dk) sürünce cihaz sinyalleriyle LIKELY_ASLEEP | Erişilebilir | DONE | PA | P2 | 307 | B48 | app/presence/engine.py; app/ambient/ingest.py | test_camera_b48.py (gece: RESTING sonra LIKELY_ASLEEP; öğleden sonra daha geç) | qualify-device-camera.ps1 G11 | no | — |
| 320 | Multiple-monitor power | Her monitör kendi DDC/CI güç kipini okur (yalnız istendiğinde, yazma yok); display_status probe_power ile monitör başına power ve açık kalan sayısı | Tam | PARTIAL | PR | P2 | — | B48 | MonitorInventory.cs; DisplayPowerController.cs | MonitorPowerTests (10) · yapısal VCP-yazma yasağı | bu makinede 2 monitör, ikisi power=on (DDC okuması, 2026-09-16) | donanım yargısı: qualify-device-camera.ps1 G12 | Monitörün gerçekten karardığını yalnız sahip görür |
| 326 | Device-local presence provider | Girdi + kamera: cihaz yalnız 7 alanlı türetilmiş gözlemi kalp atışıyla yollar; Cloud Core aynı sınırdan geçirir, kip kapalıyken veya Göz kapalıyken reddeder | Tam | DONE | PA | P2 | 327 | B48 | Camera/CameraPresenceMonitor.cs; HeartbeatStatus.cs; app/devices/status.py; app/ambient/ingest.py | CameraWiringTests (servis-boru-companion-kalp atışı uçtan uca) · test_camera_b48.py | sources:["camera"] qualify-device-camera.ps1 G2 | no | Ham kare yok: bellekte işlenir, sıfırlanır; Session-0 servis iç içe nesneyi skalerlere indirger |
| 327 | Browser-independent camera provider | Windows.Media.Capture MediaFrameReader (önce paylaşımlı salt okunur) + Windows.Media.FaceAnalysis.FaceDetector, hesapsız, cihazda; tepside gösterge; sahip tepsiden kapatabilir | Cihazda kamera | PARTIAL | PA | P2 | — | B48 | Camera/WindowsCamera.cs; Camera/WindowsCameraSurroundings.cs | CameraWiringTests (yüz geçişi sentetik bitmap üzerinde bu makinede gerçek FaceDetector ile) | qualify-device-camera.ps1 G2-G10 | fiziksel kamera değerlendirmesi | Gerçek yakalama yolu derlendi, otomatik testte kamera açılmaz (kural) |
| 331 | Ambient policy UI | Kokpit ve Ayarlar'da dört anahtar + cihaz kamerası kipi (kapalı / periyodik / sürekli) aynı PUT ile; eşikler ve sessiz saatler hâlâ salt okunur | Tam | PARTIAL | PA | P2 | 685 | B48 | CockpitPanels.tsx:AmbientPanel; lib/cockpit/api.ts:updateAmbientCameraMode | b48-web.test.tsx · b48-camera-web.test.tsx · test_camera_b48.py (PUT camera_mode, 422) | tarayıcıda görsel doğrulama | no | Eşik düzenleme ayrı iş |
| 333 | "Uyurken ekranı kapat" | Cihaz kamerasının gece sinyalleri RESTING, LIKELY_ASLEEP ve gerçek tick üzerinden owner_likely_asleep nedenli desktop.display_off üretir; öğleden sonra aynı sinyaller karartmaz; kamera susunca karartmaz | Çalışır | DONE | PA | P2 | 308 | B48 | app/ambient/ingest.py; app/ambient/service.py:tick | test_camera_b48.py (gece tetikler; öğleden sonra tetiklemez; kamera susunca tetiklemez) | qualify-device-camera.ps1 G11 | no | PROVEN_REAL sahibin uyku denemesiyle |
| 369 | Desktop toast | desktop.notify artık companion'a gerçekten bağlı (B48'de bulundu: Program hiç NotifyCapabilities kurmuyordu, her toast capability_missing) | desktop.notify | PARTIAL | PA | P1 | 5 | B11 | Program.cs:BuildNotify | CameraWiringTests (kompozisyon + fabrika) | cihazda kurulum bekliyor | no | Mutasyon C10 kırmızı |
| 671 | Camera permission | Kip varsayılan kapalı ve yalnız sahip açar; Göz kapalıyken cihaz kamerası da kapanır; Windows kamera anahtarları her açılıştan önce okunur, reddedilen kamera anahtar adıyla 'blocked' bildirilir; izin OKUNAMAZSA kamera açılmaz ('blocked', consent_unreadable = izin okunamadı); sahibin tepsi vetosu yeniden başlatmadan sağ çıkar (okunamayan veto dosyası = veto); CameraEnabled=false cihazda kalıcı kapatma | Tam | DONE | PA | P2 | 327 | B48 | Camera/CameraPresenceMonitor.cs; CameraVetoStore.cs; Camera/WindowsCameraSurroundings.cs:WindowsCameraConsent; app/ambient/camera.py | CameraPresenceTests (izin reddi, okunamayan izin, cihaz anahtarı, tepsi vetosu, yeniden başlatmada veto, bozuk veto dosyası) · CameraWiringTests (yeniden başlayan companion boru üzerinden reddeder) · test_camera_b48.py (Göz kapatma, vetolu cihaza gönderim yok) | qualify-device-camera.ps1 G7-G9 | no | Güvenlik incelemesi 2026-09-16: iki kusur (HIGH veto kalıcılığı, MEDIUM izin açık başarısızlık) giderildi |

Unchanged: 301, 302, 310, 312, 313, 330, 332 (DONE as B48 closed them on 2026-09-15).

## 4. Proposed ADR (number to assign at merge; main checkout already has ADR-0164)

### ADR-01xx — The device camera: periodic or continuous, in the owner's session, and only derived signals leave it (2026-09-16, B48)

**Context.** Owner decision 2026-09-16 (Karar 8 answered): a periodic device-local presence
check plus an optional continuous mode. ADR-0155 left 300, 307, 308, 326, 327, 333, 671
waiting on it: the sleep display-off policy requires fresh camera perception, and nothing but
a camera may say "resting".

**Decision.**

1. **Where.** Capture runs only in the Session Companion. The Session-0 service routes
   `desktop.camera_mode` and projects the heartbeat; it has no capture code.
2. **How.** Windows.Media.Capture `MediaFrameReader` (shared read-only first, so an owner's
   video call keeps the camera) and Windows.Media.FaceAnalysis `FaceDetector` — shipped with
   Windows, no account, no cloud vision. This needs the WinRT projection, so the companion's
   target framework carries the Windows SDK version (`net10.0-windows10.0.19041.0`) with its
   output folder pinned to the old path. B32 chose a PowerShell child for OCR to avoid this
   change; a camera loop is continuous and latency-bound, so an in-process path is the right
   trade here.
3. **In memory only.** A sample is 5 frames; each becomes face boxes plus an 80×60 luma
   plane, zeroed after the sample. No encoder, sink, file or socket exists in the camera
   folder (structural test). Only the seven §2 fields and the camera's state leave the device;
   the Session-0 service drops a nested object holding anything but short scalars; Cloud Core
   screens the observation with the existing boundary.
4. **Consent.** Mode `off` after every start; only the owner's `ambient_policy.camera_mode`
   (relayed while the Active Eye is enabled) opens it; "Kamerayı kapat" closes it; Windows'
   privacy switches are read before every open and a denial is reported by name; the owner can
   veto on the device from the tray, which the cloud never argues with; `CameraEnabled=false`
   is the device-local rollback.
   *Security review (2026-09-16).* The tray veto is persisted in the owner's profile
   (`%LOCALAPPDATA%\PagentOS\companion\camera-veto.json`, write-then-move; not Session 0,
   not the cloud), read before anything else at start, and cleared only by the owner's tray
   action; an unreadable veto file is a veto. While vetoed the device reports `vetoed` from
   its first heartbeat and refuses every non-off `desktop.camera_mode` with
   `permission_denied`; Cloud Core never sends a non-off mode to a device reporting
   `vetoed`. The permission check fails CLOSED: a permission that cannot be read is
   `blocked` / `consent_unreadable` ("izin okunamadı") and nothing is opened. The veto
   store lives outside `Camera/`, whose sources stay forbidden any file API.
5. **Indicator.** Tray icon whenever a mode is on; "open" face before the device is opened
   and until after it is closed.
6. **Rest.** Present + still + 5 min with no input and no sound (render peak meter) → posture
   `resting`. A dark room is a low-confidence absence (fuses to UNKNOWN). The fusion engine's
   quiet-hours thresholds (ADR-0155 §3) and the ambient policy's gates then decide sleep and
   display-off — no new inference path in Cloud Core.
7. **Relay.** Desired mode vs reported mode is reconciled on the heartbeat, at most once a
   minute per device, as a durable command row; a mode or eye change pushes at once.
8. **Monitors (320).** Per-monitor DDC/CI power is READ on request only; writing a VCP code
   is forbidden by the display family's structural test. Visible darkness stays the owner's
   judgement.

**Consequences.** Migration 0059 (`ambient_policy.camera_mode`, default `off`). One new
always-advertised capability (manifest +1 everywhere). Companion output +~25 MB. Rows 307,
308, 326, 333, 671 DONE (PROVEN_AUTOMATED), 300/327 PARTIAL until the owner's physical run,
320 PARTIAL with a real DDC measurement. Rollback: `camera_mode=off` (cloud) or
`CameraEnabled=false` (device); input-based presence (311) is untouched.

## 5. Proposed evidence record (`docs/evidence/b48-device-camera-2026-09-16.json`)

```json
{
  "batch": "B48",
  "name": "Varlık derinliği ve kamera - cihaz kamerası (sahip kararı 2026-09-16)",
  "requirements": [300, 303, 307, 308, 320, 326, 327, 331, 333, 369, 671],
  "recorded_at": "2026-09-16",
  "owner_decision": "periodic device-local presence check + optional continuous mode; capture in the Session Companion only; frames in memory only; derived signals only; visible indicator; owner switches; honest blocked reporting",
  "tests": {
    "devices/windows-agent/tests/PagentOS.Agent.Tests/Camera/CameraPresenceTests.cs": "29 cases (incl. veto across a restart, unreadable veto store, file store, unreadable permission)",
    "devices/windows-agent/tests/PagentOS.Agent.Tests/Camera/CameraWiringTests.cs": "14 cases (restarted companion with a remembered veto refuses over the pipe; end-to-end service-pipe-companion-heartbeat, schema contract, projection, composition, structural no-frame guard, real FaceDetector on a synthetic bitmap)",
    "devices/windows-agent/tests/PagentOS.Agent.Tests/Camera/MonitorPowerTests.cs": "10 cases (+1 opt-in DDC lab)",
    "services/api/tests/unit/test_camera_b48.py": "20 cases (restarted vetoed device never sent a non-off mode; wire shape, intake guards, relay, command row, 333 night/afternoon/silent-camera end to end, PUT camera_mode)",
    "apps/web/tests/eye/b48-camera-web.test.tsx": "7 cases",
    "scripts/tests/device-camera-qualification.tests.ps1": "22 checks (dry run of the owner harness)"
  },
  "runtime_measurements": {
    "face_detector": "Windows.Media.FaceAnalysis.FaceDetector.IsSupported=true on the owner's machine; the analyzer ran on a synthetic 320x240 bitmap (no camera opened)",
    "camera_enumeration": "one color source (Logi C615 HD WebCam, VideoRecord) - enumeration only, nothing opened",
    "consent_store": "webcam and NonPackaged values = Allow (read only)",
    "ddc_power_read": "2 monitors (2560x1440, 1440x2560 primary), both answered VCP 0xD6 power=on",
    "manifest": "12 / 13 / 43 / 103 capabilities (none / -DisplayPower / + browser / + -Operator)"
  },
  "owner_gates": {
    "script": "scripts/core/qualify-device-camera.ps1",
    "gates": "G1-G12 (G9 with -IncludePrivacyCheck, G11-G12 with -SleepTrial)"
  },
  "found_on_the_way": {
    "desktop_notify_unwired": "Program.cs never built NotifyCapabilities, so desktop.notify (advertised on every device since B11) answered capability_missing; fixed, regression + mutation C10",
    "legacy_office_format": "dotnet format --verify-no-changes failed on HEAD in Documents/LegacyOfficeExtractor.cs (B52 whitespace); reformatted, whitespace only",
    "redundant_film_guard": "the first classifier refused rest for sound twice (still-run reset AND the resting condition); mutation C4b could not turn red because the second was dead code - removed, and the film test now also proves the still run restarts when the sound stops",
    "unity_lab": "SceneUnityTests.The_real_unity_editor... fails on this machine (a licensed editor exits 1); work in progress in the main checkout (ADR-0164), not touched"
  }
}
```

## 5b. Red-first proofs (24/24)

Executed mutations, each restored from an in-memory copy whose sha256 was verified
(scratchpad `b48_mutate.py`; never `git checkout`):

- P1 a reading on its way after the owner's OFF still enters the model -> red
- P2 the camera path accepts an observation claiming another source -> red
- P3 the relay argues with the owner's tray veto -> red
- P4 the relay re-sends on every heartbeat -> red
- P5 a disabled eye leaves the device camera open -> red
- P6 a far-future stamp becomes the newest reading -> red
- P7 an unknown camera mode is persisted -> red
- P8 the device row repeats an unscreened observation -> red
- P9 heartbeats never feed the camera into fusion (333 cannot fire) -> red
- P10 the quiet hours no longer set the sleep threshold -> red
- W1 the panel sends a camera mode no device understands -> red
- W2 the mode buttons appear where the panel must stay read-only -> red
- W3 a blocked camera reads as merely closed -> red
- C1 the Session-0 service forwards a nested presence object as-is -> red
- C2 the indicator goes up only after the camera is opened -> red
- C3 frames are not zeroed after the sample -> red
- C4 a film is sleep (sound no longer restarts the still run) -> red (after the test was strengthened; the first run stayed green and exposed a redundant guard, now removed)
- C5 input during the still run does not restart it -> red
- C6 a reading from before OFF survives into the next ON -> red
- C7 Windows' camera switch is not read before opening -> red
- C8 the owner's tray veto is ignored -> red
- C9 a plain display status reads the monitor bus -> red
- C10 the shipped companion hands the runtime no toast object (the B11 bug) -> red
- C11 the device-local switch no longer keeps the camera closed -> red

## 5b-2. Security review fixes: red-first proofs (10/10)

- R1 the remembered veto is not loaded at start -> red
- R2 the tray veto is not written to the owner's profile -> red
- R3 a non-off mode is accepted while vetoed -> red
- R4 a remembered veto is not reported before the first loop pass -> red (first aimed at the pipe test, which stayed green because the loop's first pass also reports the veto; re-aimed at the restart unit test, which reads the status before any pass)
- R5 an unreadable veto store is 'no veto' -> red
- R6 a damaged veto file reads as 'no veto' -> red
- R7 the shipped companion builds its monitor without the profile veto store -> red
- R8 an unreadable permission is 'allowed' -> red
- R9 the Cloud relay sends a non-off mode to a vetoed device -> red
- R10 the panel shows an unreadable permission as a raw token -> red

## 5c. Gates run

- `dotnet build -c Release`: 0 warnings, 0 errors. `dotnet format --verify-no-changes`: clean.
- `PagentOS.Agent.Tests`: 1051 passed, 1 skipped, 1 failed — the failure is the pre-existing
  real-Unity lab (`SceneUnityTests.The_real_unity_editor...`, a licensed editor exits 1 on
  this machine; the main checkout carries uncommitted work on exactly that test, ADR-0164).
  Camera folder: 25 + 13 + 10 = 48 passed; after the security review fixes 29 + 14 + 10 = 53, whole project 1056 passed / 1 skipped / 1 failed (the same Unity lab). `PagentOS.Companion.Audio.Tests`: 135/135.
- `scripts/qualify-staged-update.ps1`: STAGED UPDATE QUALIFIED, 89 checks, 43 capabilities.
- PowerShell suites: device-camera-qualification 22/22, agent-update 33/33,
  installer-release 12/12, script-syntax (110 scripts), harness-symbols 143/143,
  item28-gate 46/46, owner-harness 17/17.
- Cloud Core: `test_camera_b48.py` 20/20 (after the review fixes; presence/ambient/devices/mirror/camera set 337 passed); presence/ambient/devices/alarms/broker/migration/
  mirror/identity/world/routines-presence set 972 passed; `ruff check app` clean.
- Web: vitest full suite 1893/1893 (106 files); `tsc --noEmit` clean; oxlint on the changed
  files clean.
- Browser check: not run — the ambient panel sits behind the owner sign-in and a live Cloud
  Core (the agent does not enter the owner credential); the panel is proven by
  `b48-camera-web.test.tsx` rendered with react-dom/server.

## 6. OWNER CHECKPOINT (READY_FOR_OWNER — one sitting, no UAC)

Prerequisites: this branch merged, the Cloud Core released (migration 0059), the Windows
agent staged/installed with the new companion (the usual `install-device-service.ps1`
flow; no new switch), the owner signed in to the desktop.

1. `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\core\qualify-device-camera.ps1 -DryRun`
   (prints the plan, sends nothing; must end `VERDICT: PLANNED`).
2. `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\core\qualify-device-camera.ps1 -IncludePrivacyCheck`
   (about 10 minutes). Answer the Turkish prompts: tray icon visible (G3), camera light and
   "AÇIK" icon on (G4), leave the frame and come back (G5), light and icon gone (G6), use the
   tray menu to close and re-allow the camera (G8), switch "Masaüstü uygulamalarının kameraya
   erişmesine izin ver" off and on (G9). Expected: `VERDICT: PASS`.
3. Optional, in the evening inside the quiet hours (or allow ~70 minutes outside them):
   `... qualify-device-camera.ps1 -SleepTrial` — sit still facing the camera with the room
   lit, no keyboard/mouse, no sound, until the screens go dark; answer whether EVERY monitor
   went dark before pressing a key (G11 = rows 307/308/333 PROVEN_REAL, G12 = row 320).
4. Send the evidence file `docs\evidence\b48-device-camera-<timestamp>.json` back (it holds
   only states, tokens and timestamps — no image).

To turn the camera off at any time: the panel's "Cihaz kamerası: kapalı", "Kamerayı kapat",
or the tray menu; permanently on the device: `PAGENTOS_AGENT_CameraEnabled=false`.
