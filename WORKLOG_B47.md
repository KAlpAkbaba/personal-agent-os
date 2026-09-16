# WORKLOG B47 — Cihaz tarafı ses (device-side voice)

Owner decision (2026-09-16, final): **continuous listening on the device**, with the privacy rules
of the batch brief. This file carries what the integrator writes into the files this worktree
must not touch: the proposed matrix rows, the proposed ADR, the owner checkpoint, and the facts
behind them. Nothing here was deployed; the device installer was not run.

## 1. What was measured before building

- **The B47 evidence file's scan was wrong.** `docs/evidence/b47-device-voice-owner-decision-2026-09-15.json`
  says the device has no capture path. It has one: `PagentOS.Companion.Audio` (M12 track C) opens the
  microphone through WASAPI (`WasapiCaptureDevice`), and `PagentOS.SessionCompanion.exe --voice` /
  `PAGENTOS_AGENT_VoiceEnabled=true` + `PAGENTOS_AGENT_CloudCoreUrl` runs a full realtime client
  (`VoiceSessionOrchestrator`) against the Cloud Core's session/sideband API.
- **That client streamed silence.** `VoiceClientOptions.StreamWhileIdle = true` and the host handed the
  orchestrator the raw microphone, so every captured frame — the silent room included — went to the
  provider for as long as voice was enabled. That contradicts the owner's rule ("silence never leaves
  the device"). B47 builds on the client and closes this (see §6b, bug 1).
- **No offline Turkish recogniser exists on this machine.** SAPI desktop and OneCore list only
  `en-US` recognizers; no speech NuGet package is in the offline cache; Windows offers no `tr-TR`
  speech-recognition pack. So the offline engine is an in-house, no-account, no-download
  **MFCC + DTW template spotter** over the owner's own enrolled recordings (speaker-dependent; it
  matches words and authorises nothing). Row 241 is therefore not BLOCKED_PROVIDER, but its quality
  is only measurable on the owner's voice (PARTIAL until the checkpoint).
- **The companion's other status channel exists**: the heartbeat's `status` object
  (`desktop.activity_status`, §6g). Voice health rides it; no new transport was added.

## 2. What was built

Device (Session Companion only — the Session-0 service references no audio assembly; a test reads
its project and sources):

- `Listening/DeviceListeningService` — owns the microphone for the life of the companion. One loop.
  Continuous mode: the local VAD (the M12 energy VAD + Turkish hesitation guard) opens an utterance;
  only the utterance (≤ 300 ms pre-roll + speech + the trailing silence the detector needed) is
  forwarded. Wake-word mode: an open-end DTW watch at the start of each breath; the wake word itself
  is zeroed, the rest of the breath is the request; 8 s follow-up window and barge-in while the
  assistant is audible. Push-to-talk: capture opens only while right Ctrl is held ≥ 150 ms
  (`PAGENTOS_AGENT_VoicePushToTalkKey`), the key release ends the turn.
- `PreRollBuffer` — the only home of un-admitted audio: ≤ 2000 ms (clamped), zeroed as it falls out.
- `GatedCaptureTap` / `GatedCaptureFactory` — the realtime orchestrator sees the gate's tap, never
  the microphone; turn boundaries come from the gate (`IUtteranceBoundarySource`); the orchestrator
  uplinks only inside a gate-opened turn; the device client runs `EndOfTurn=Client`,
  `StreamWhileIdle=false` (`VoiceCompanionHost.ClientOptionsFor`).
- `DeviceVoiceHost` — sessions laid over the listener: replaced after failure/expiry with backoff,
  owner token looked for again, connectivity copied into the listener and the health.
- `DeviceVoiceHealth` — `desktop.voice_status` report + the heartbeat `voice` object.
- `Spotting/` — `MfccExtractor`, `Dtw`, `TemplateKeywordSpotter` (thresholds measured from the
  templates, margin rule), `KeywordTemplateSet` (only the contract's phrase ids), DPAPI-protected
  `FileKeywordTemplateStore` (`<DataDir>\voice\keywords.bin`), `KeywordEnrollment`,
  `EnrollmentRecorder`. `PagentOS.SessionCompanion.exe --voice-enroll [ids] [--takes N]` is the only
  place recordings are taken; the running companion reloads templates without a restart.
- `WasapiMuteMonitor` (endpoint mute or zero master level; unreadable = `null`), `Win32PushToTalkKey`
  (`GetAsyncKeyState`, no hook).
- `Voice/TrayPrivacyIndicator` — tray icon (off / muted / listening / wake_word / push_to_talk /
  sending) with the owner's switch, the mode, "Çalan alarmı ertele", "Çalan alarmı durdur".
- `Voice/OfflineVoiceCommands` — `alarm.stop`, `alarm.snooze`, `time.tell` (toast); `listening.off`
  is flipped by the listener itself. Offline-only commands act only while the Cloud Core is
  unreachable; `listening.off` always.
- `AlarmArmController.SnoozeRinging` / `DrainLocallySnoozed` — the local snooze on the cloud's terms
  (`snooze_minutes`, `snoozes_left` on arm and ring), reported as `local_alarm_snoozed`.
- `desktop.voice_status` (ambient group, always advertised; read-only; there is no name that opens a
  microphone). Manifest: 12 always-on names, 43 with `-DisplayPower` + browser, 103 with every gate.
- `Program.SuperviseVoiceAsync` — the voice service restarts after a crash, each counted.

Cloud Core:

- `app/devices/voice_contract.py` (bundled `device-voice.json`), `status.py` parses `voice` and
  `local_alarm_snoozed` (normalised, bounded, diffed), shows them on `GET /v1/devices`.
- `ambient/ingest.py` reconciles fired **then** snoozed; `alarms.service.reconcile_local_snoozed`
  adopts the device's `until` through the ordinary snooze (`snooze_alarm(resume_at=…)`), records
  `alarm.local_snoozed` either way; `sequence.py` sends the snooze terms on arm and tone ring.
- `CAPABILITY_DESKTOP_VOICE_STATUS` in `routines/dispatch.py` (the desktop mirror test knows it).

Shared contract: `packages/protocol/device-voice.json` (pre-roll/segment bounds, modes, indicator
and service states, heartbeat keys, offline command table, local-snooze shape), registered in
`test_contract_falsification.py`; the device half is `DeviceVoiceContractTests`, the cloud half
`test_device_voice_contract.py` (which also reads `DeviceVoiceContract.cs` and `HeartbeatStatus.cs`).
Schema: `deviceStatus.voice` and `deviceStatus.local_alarm_snoozed`. `DEVICE_PROTOCOL.md` §6g, new
§6o, §9. Owner scripts: `qualify-staged-update.ps1` gate 3 expects `desktop.voice_status`;
`qualify-item28-unlocked.ps1` knows the ten status keys and the 103 count.

## 3. Proposed feature-matrix rows (replace rows 239-244, 250-255 and 259)

Every row has 15 `|`; no cell contains a pipe.

```
| 239 | Device-side microphone provider | Companion WASAPI yakalama + `desktop.voice_status` manifestte (her zaman) | Cihazda mikrofon | DONE | PA | P2 | — | B47 | packages/protocol/device-voice.json, .../Listening/DeviceListeningService.cs, .../Wasapi/WasapiAudioBackend.cs, ProtocolConstants.cs:DesktopVoiceStatus | DeviceListeningTests.cs (26), DeviceVoiceCapabilityTests.cs (12), DeviceVoiceContractTests.cs (6), test_device_voice_contract.py (6) | sahip mikrofon turu bekliyor (qualify-device-voice.ps1 P1-P6) | yes | Karar 7 kabul: SÜREKLİ dinleme (2026-09-16). B47 kanıt dosyasının "yakalama yolu yok" taraması yanlıştı: M12 istemcisi yakalıyordu ve sessizliği de gönderiyordu |
| 240 | Browser-independent listening | Companion ses servisi tarayıcısız dinler; gerçek zamanlı oturum yalnız cihaz kapısının geçirdiğini görür | Tarayıcısız dinleme | DONE | PA | P2 | 239 | B47 | .../Listening/DeviceVoiceHost.cs, .../Listening/GatedCapture.cs, VoiceCompanionHost.cs | DeviceVoiceHostTests.cs (7) | PROVEN_REAL bekliyor: tarayıcı kapalıyken komut (qualify-device-voice.ps1 B1-B2) | yes | Tüm Chrome/Edge kapalı, windows_desktop oturumu utterance + yanıt kaydetmeli |
| 241 | Wake word | Çevrimdışı, hesapsız MFCC+DTW şablon motoru; sahibin kaydıyla; uyandırma sözcüğü cihazdan hiç çıkmaz | Uyandırma sözcüğü | PARTIAL | PA | P2 | 240 | B47 | .../Listening/Spotting/*.cs, DeviceListeningService.cs (wake watch) | SpottingTests.cs (11), DeviceListeningTests.cs (wake: 4) | yanlış uyanma/saat ve isabet ölçülmedi (qualify-device-voice.ps1 W1-W5) | yes | Makinede Türkçe tanıyıcı YOK (yalnız en-US SAPI/OneCore). Konuşmacıya bağlı; kimlik doğrulaması DEĞİL. Kalite yalnız sahibin sesiyle ölçülebilir |
| 242 | Wake-word enable/disable | Mod anahtarı (sürekli / uyandırma / bas-konuş) kalıcı; kayıtsız sözcükle mod reddedilir | Açılıp kapanır | DONE | PA | P2 | 241 | B47 | .../Listening/ListeningSettings.cs, TrayPrivacyIndicator.cs | DeviceListeningTests.cs (mod: 3) | sahip turu (W1, W5) | no | Kayıtlı seçim kayıtsız sözcükle bas-konuş olarak çalışır ve sağlık bunu söyler |
| 243 | Push-to-talk fallback | Sağ Ctrl ≥150 ms basılıyken mikrofon açık; bırakınca tur biter | Bas-konuş | DONE | PA | P2 | 239 | B47 | DeviceListeningService.cs:HandlePushToTalk, DeviceInputs.cs:Win32PushToTalkKey | DeviceListeningTests.cs (2) | sahip turu bekliyor | no | Tuşlar arasında hiçbir şey yakalanmaz; kanca yok, yalnız GetAsyncKeyState |
| 244 | Local VAD | Cihaz kapısı: yalnız yerel dedektörün açtığı söz gider; sessizlik ≤2 s halkada sıfırlanır | Cihazda konuşma tespiti | DONE | PA | P2 | 239 | B47 | DeviceListeningService.cs, PreRollBuffer.cs, VoiceSessionOrchestrator.cs:HandleBoundaryAsync | DeviceListeningTests.cs, DeviceVoiceHostTests.cs (uçtan uca: sessizlikte 0 kare) | sahip turu (V1-V2) | no | Bulunan kusur: M12 istemcisi StreamWhileIdle=true ile odadaki sessizliği sağlayıcıya akıtıyordu |
| 250 | Device-local Voice startup | VoiceEnabled ile companion açılışında başlar; token yoksa cihazda dinlemeyi sürdürür | Açılışta başlar | DONE | PA | P2 | 239 | B47 | Program.cs:RunVoiceAsync, DeviceVoiceHost.cs | DeviceVoiceHostTests.cs, DeviceVoiceCapabilityTests.cs | sahip turu (P3, -EnableVoice) | yes | Kurucu VoiceEnabled yazmıyor; sahip betiği kullanıcı ortamına yazar |
| 251 | Voice service restart recovery | Oturum hatası/süre dolumu backoff ile yenilenir; servis çökmesi denetçiyle yeniden başlar; kapanışta mikrofon her durumda kapanır | Toparlanır | DONE | PA | P2 | 250 | B47 | Program.cs:SuperviseVoiceAsync, DeviceVoiceHost.cs | DeviceVoiceHostTests.cs, DeviceVoiceCapabilityTests.cs (2), DeviceListeningTests.cs (iptal) | sahip turu bekliyor | no | Önceden tek istisna sesi süreç ömrü boyunca bitiriyordu; token yoksa ses kalıcı kapalıydı |
| 252 | Voice process health | `desktop.voice_status` + heartbeat `voice` (8 anahtar) + GET /v1/devices heartbeat_status.voice | Sağlık görünür | DONE | PA | P2 | 250 | B47 | DeviceVoiceHealth.cs, HeartbeatStatus.cs, app/devices/status.py, app/devices/voice_contract.py | DeviceVoiceCapabilityTests.cs, test_device_voice_contract.py | sahip turu (P5-P6) | no | Yalnız durum/bayrak/sayaç/hata SINIFI; döküm, ifade, aygıt adı, ses asla |
| 253 | Offline command subset | alarm.stop, alarm.snooze, time.tell (yalnız bulut erişilemezken), listening.off (her zaman); sözleşme tablosu | Çevrimdışı komutlar | PARTIAL | PA | P2 | 240 | B47 | device-voice.json:offline_commands, OfflineVoiceCommands.cs, DeviceListeningService.cs:ClassifyOfflineCommand | DeviceListeningTests.cs (4), LocalSnoozeTests.cs (7) | tanıma sahibin sesinde ölçülmedi (O3-O5) | yes | Yürütme ve politika tam; Türkçe tanıma kalitesi fiziksel turda. Bir cümle asla iki kez etki etmez |
| 254 | Voice privacy indicator | Tepsi simgesi: off/muted/listening/wake_word/push_to_talk/sending; menüde anahtar | Dinleme göstergesi | DONE | PA | P2 | 240 | B47 | TrayPrivacyIndicator.cs, DeviceListeningService.cs:UpdateIndicator | DeviceListeningTests.cs (gösterge durumları), DeviceVoiceCapabilityTests.cs (etiket/renk) | sahip turu (V3-V4) | no | "sending" yalnız söz bağlı bir oturuma giderken. Açma yalnız cihazda; uzaktan açma reddedilir |
| 255 | Hardware mic mute awareness | Uç noktanın susturma bayrağı 500 ms'de okunur; susturulunca yakalama akışı durur; okunamazsa null | Donanım susturması bilinir | PARTIAL | PA | P2 | 239 | B47 | Wasapi/WasapiMuteMonitor.cs, DeviceListeningService.cs:PollMute | DeviceListeningTests.cs (3) | hangi tuşun uç noktaya yazdığı ölçülmedi (V4) | yes | Mantık PA; gerçek WASAPI okuması ve dizüstü tuşu yalnız sahip turunda. 3 s tam sıfır 'digital_silence' olarak raporlanır |
| 259 | Snooze | Yerel tetikleyici: çevrimdışı "ertele" + tepsi; bulutun koşullarıyla; `local_alarm_snoozed` ile raporlanır, bulut cihazın anını benimser | Tam | DONE | PA | P1 | — | B13, B47 | app/alarms/service.py:reconcile_local_snoozed, app/ambient/ingest.py, AlarmArmController.cs:SnoozeRinging, device-voice.json:local_snooze | test_alarms_local_snooze.py (6), LocalSnoozeTests.cs (7), test_alarms_service.py (51) | üretim turu bekliyor (O1-O6) | no | Sınır (5) cihazda da bulutun sayısıyla. Bayat disarm yerel ertelemeyi SİLEMEZ (bulunan kusur) |
```

Test counts in the rows are this worktree's, by file (§5).

## 4. Proposed ADR (number assigned at integration; 0164 is already taken in the main checkout)

```
## ADR-0165 — ADR-0154 accepted: the device listens continuously, and only speech leaves it (2026-09-16, B47)

**Status.** ACCEPTED (owner, 2026-09-16) - supersedes ADR-0154's PROPOSED status with a fourth
option the owner chose: continuous listening WITHOUT a wake word by default, gated on the device.

**Context.** ADR-0154 asked the owner to choose push-to-talk, a local wake word, or neither.
Measured while building: the device already had a capture path (M12 track C) and, when enabled,
streamed every microphone frame - silence included - to the realtime provider; no offline
Turkish recogniser is installed or installable without an account on the owner's machine.

**Decision.**
1. The Session Companion owns the microphone (never the Session-0 service) for as long as voice
   is enabled and the owner's switch is on. A local voice-activity gate decides what leaves:
   only an utterance, with at most 300 ms of pre-roll and the trailing silence the end-of-turn
   detector needed. Silence never leaves. The realtime client sees the gate's tap, commits its
   own turns and never streams between them.
2. Raw audio is never written or logged (row 249 unchanged). Un-admitted audio lives in a ring of
   at most 2000 ms and is zeroed as it falls out; a wake-word segment is bounded the same way.
3. The owner turns listening on only at the device; anything may turn it off. The persisted
   switch loads as OFF when unreadable. A detected endpoint mute stops the capture stream.
4. Modes: continuous (default), wake word (optional), push-to-talk (fallback). The wake word and
   the offline commands use an in-house MFCC+DTW template engine over the owner's own enrolled
   recordings, stored as DPAPI-protected features only. It is a word matcher, never an identity
   check, and the commands it may trigger only stop, defer or report (device-voice.json).
5. Offline-only commands act only while the Cloud Core is unreachable, so one sentence never acts
   twice; "listening.off" always may. The realtime leg declares itself dead after 5 s + 10 s of
   unanswered pings so "unreachable" is known in seconds.
6. A local snooze uses the alarm row's terms (snooze_minutes, snoozes_left sent on arm and ring),
   reports {alarm_id, until}, and the cloud adopts `until` through its ordinary snooze. A disarm
   cannot delete a local snooze the cloud has not yet been told about.
7. Health (`desktop.voice_status`, heartbeat `voice`) carries states, flags, a restart counter and
   an error class only. There is no capability that opens a microphone from the cloud.

**Consequences.** One capability name (manifest 103 with every gate). One shared contract
(`packages/protocol/device-voice.json`). The physical evaluation - silence, speech, switch, mute,
wake-word false wakes, the browser-less command, the offline snooze - is the owner's
(`scripts/core/qualify-device-voice.ps1`, OWNER_REQUIRED); rows 241, 253, 255 stay PARTIAL until it
passes. Voice is enabled per user environment (PAGENTOS_AGENT_VoiceEnabled, CloudCoreUrl); the
installer does not write them.
```

## 5. Tests added (this worktree)

| Suite | File | Tests |
|---|---|---|
| Companion.Audio.Tests | DeviceListeningTests.cs | 26 |
| Companion.Audio.Tests | SpottingTests.cs | 11 |
| Companion.Audio.Tests | DeviceVoiceHostTests.cs | 7 |
| Companion.Audio.Tests | DeviceVoiceContractTests.cs | 6 |
| Agent.Tests | Voice/DeviceVoiceCapabilityTests.cs | 12 |
| Agent.Tests | Alarms/LocalSnoozeTests.cs | 7 |
| services/api | tests/unit/test_device_voice_contract.py | 6 |
| services/api | tests/unit/test_alarms_local_snooze.py | 6 |
| services/api | test_contract_falsification.py (device-voice.json registered) | +5 parametrised |

Suite totals after B47 (this machine, Release): Companion.Audio.Tests **185/185** (was 135);
Agent.Tests **1022 passed, 1 skipped, 1 failed of 1024** - the failure is the real Unity editor test
(§8), unrelated to B47. services/api: the 21 affected unit modules, 418 passed; `ruff check` clean;
`sync-protocol-bundle.py --check` clean. `scripts/qualify-staged-update.ps1`: 89 checks passed
(43 names with -DisplayPower). Solution build: 0 warnings, 0 errors.

## 6. Mutation proofs

Each guard was run once against a mutated tree, restored from a sha256-verified byte backup
(never `git checkout`); all 18 went RED.

| Id | Guard | Mutation | Result |
|---|---|---|---|
| M1 | silence never leaves the device (244) | continuous branch forwards instead of buffering | 1 of 2 red |
| M2 | a gated session uplinks only inside a gate-opened turn | `if (_turnOpen)` -> `if (true)` | 1 of 1 red |
| M3 | a detected hardware mute stops capture (255) | `StopCapture("mic_muted")` removed | 1 of 1 red |
| M4 | listening cannot be turned ON remotely | remote refusal never matches | 1 of 1 red |
| M5 | offline-only commands stay silent while the cloud is reachable (253) | offline-only rule never matches | 1 of 1 red |
| M6 | audio falling out of the pre-roll is zeroed (249) | `Zero(old)` removed | 2 of 3 red |
| M7 | a cancelled caller still closes the microphone (bug 4) | loop tied to the caller's token again | 1 of 1 red |
| M8 | a stale disarm cannot delete an unreported local snooze (bug 5) | protection never matches | 1 of 1 red |
| M9 | the shipped companion builds desktop.notify's executor (bug 2) | `notify: notify,` removed | 1 of 1 red |
| M10 | the realtime leg notices a dead network (bug 6) | `KeepAliveTimeout` line removed | 1 of 1 red |
| M11 | the device never invents snooze terms (259) | `minutes ??= 9; left ??= 5;` | 1 of 1 red |
| M12 | the cloud adopts the device's snooze instant | `resume_at` ignored | 1 of 5 red (first attempt stayed GREEN: the test's report arrived a whole number of minutes late; the test now uses 4 min 25 s) |
| M13a | the cloud half reads device-voice.json | `max_preroll_ms` 2000 -> 3000 | 2 of 6 red |
| M13b | the device half reads device-voice.json | same | 1 of 6 red |
| M14 | the heartbeat carries the voice health (250-252) | key renamed | 2 of 2 red |
| M15 | the wake word itself never leaves the device (241) | wake-word frames forwarded | 1 of 1 red |
| M16 | a phrase over its measured threshold is not a command | threshold check removed | 2 of 12 red |
| M17 | the cloud ingests the fired report before the snooze | fired reconcile skipped | 1 of 1 red |

## 6b. Real bugs found on the way (each with a regression test)

1. **Privacy: the M12 device client streamed the silent room to the provider** while voice was
   enabled (`StreamWhileIdle = true`, raw microphone handed to the orchestrator). Fixed by the gate +
   `ClientOptionsFor`. `DeviceVoiceHostTests.The_device_composition_uplinks_nothing_while_the_room_is_silent...` (M1).
2. **B11 wiring: `desktop.notify` was advertised by every companion but never built by `Program`**, so
   the shipped process answered its own name with `capability_missing` (row 369 was only true in
   tests that built the runtime by hand). `DeviceVoiceCapabilityTests.The_shipped_companion_hands_the_runtime_every_always_advertised_executor` (M9).
3. **One voice exception ended voice for the life of the process; a missing owner token ended it
   permanently.** Supervised restart + token retry. `A_crashed_voice_service_is_restarted...`,
   `Without_an_owner_token_the_device_keeps_listening_offline_and_looks_again`.
4. **A cancelled caller left the microphone open and the indicator saying "listening"** (the loop
   shared the caller's token and ended before its shutdown). Found by the host test. M7.
5. **A stale cloud disarm could delete an offline snooze** (the cloud queues a 10 s disarm when it gives
   up on an unreachable device; a network returning inside that window erased the deferred wake-up). M8.
6. **The realtime WebSocket leg had no keep-alive timeout**, so a pulled network went unnoticed until TCP
   gave up and offline commands stayed off. M10.
7. **The first-uplink latency metric would have counted pre-roll age** (frames captured before the gate
   opened); measured from the gate's decision now (asserted in the composition test).
8. **Harness drift:** `qualify-item28-unlocked.ps1` still named "exactly the seven documented keys"
   after B13 made them eight; it now lists all ten.

## 7. Owner checkpoint (OWNER_REQUIRED) — the physical microphone evaluation

Script: `scripts/core/qualify-device-voice.ps1` (PASS only when every gate of every selected phase
passes; evidence JSON with `-OutFile`).

Steps for the owner:

1. Prerequisites: the device agent from this commit installed (the usual install command; this
   batch did not run it); the Cloud Core released with this commit (for the snooze terms and the
   `voice` status parsing); a working microphone and speakers; about 25 minutes.
2. `.\scripts\core\qualify-device-voice.ps1 -EnableVoice -OutFile b47-device-voice-1.json`
   (first run: `-EnableVoice` writes `PAGENTOS_AGENT_VoiceEnabled` / `PAGENTOS_AGENT_CloudCoreUrl`
   into the user environment and restarts the "PagentOS Session Companion" task).
3. **enroll**: press Enter, say each prompted phrase (your wake word, "ertele", "alarmı kapat",
   "dinlemeyi kapat", "saat kaç") three times. Only features are stored.
4. **privacy**: stay silent 30 s (V1); say the given sentence (V2); turn listening off from the tray
   icon, speak, turn it on (V3); mute the microphone in hardware, speak, unmute (V4); V5 is checked
   automatically.
5. **wake**: select "Uyandırma sözcüğü" in the tray; 5 sentences without it; 5 × wake word + request;
   then `-SoakMinutes` (default 10) of ordinary room sound without addressing the device; select
   "Sürekli" again.
6. **browserless**: close every browser window; say "Saat kaç?" to the device and listen.
7. **offline**: the script creates a 90 s test alarm (snooze 2 min). When the cloud rings it, pull the
   network, wait for the tray tooltip to say "Cloud Core'a bağlı değil", say "ertele"; still offline,
   say "saat kaç" (a toast shows the time); reconnect; wait for the snoozed alarm to ring again; stop
   it ("Alarmı kapat" or the tray's "Çalan alarmı durdur").

Gates and thresholds: P1-P6, E1-E3, V1 (0 utterances and 0 admitted frames in 30 s of silence),
V2, V3a/b, V4a/b, V5 (no audio file, no spoken word in any log line), W1, W2 (0 of 5), W3 (≥ 4 of 5),
W4 (≤ 2 false wakes per hour), W5, B1, B2, O1-O6.

## 8. Known limits (said, not hidden)

- The template engine is speaker-dependent and untested on real Turkish speech; its thresholds are
  measured from the owner's three takes and may need a re-enroll in a different room.
- The Cloud Core does not learn about a LOCAL stop ("alarmı kapat" offline, or the tray's stop); an
  alarm it still believes is playing completes at its own play expiry. No double ring results.
- When the Cloud Core gave up on an unreachable device (FAILED), a later local snooze is recorded
  (`alarm.local_snoozed`, `alarm_local_snooze_not_adopted`) but not resurrected; the device still
  rings at `until` on its own arm.
- The OpenAI session is created with server `semantic_vad`; the device client's session.update sets
  client turn detection with the beta-shaped key. If the provider ignores it, both sides may
  commit; the physical run (B2) shows whether answers arrive once.
- `SceneUnityTests.The_real_unity_editor_answers_either_the_licence_refusal_or_the_run_and_its_inspection`
  fails on this machine (the real Unity editor exits 1); B47 touches nothing in that area and the
  main checkout is editing that test.

## 9. Independent privacy review (security-reviewer, commit 8445fb3)

No Critical or High findings. Low note 1 applied in the follow-up commit: a failing local-snooze
reconcile now logs the error class, a bounded message and the entries (alarm ids and instants
only), with a test. Low note 2 stands as a known limit: V5's "no transcript on disk" is a
log-grep over the companion log for the spoken probe words.
