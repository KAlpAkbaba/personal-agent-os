# M18.3 — Fullscreen Living Core, durable Wake Alarm, ambient display control

Owner directive 2026-09-07 ("OWNER PRIORITY — FINISH M18.2, THEN M18.3"). This document is
the architecture and the cross-track contract. Four tracks implement it in parallel; every
name, payload and state below is shared between them and must not be renamed by one track
alone. ADR-0069 records the decisions; ADR-0070..0073 are reserved for the tracks (see §10).

Everything M18 proved stays proved: the action contract (`docs/M18_ACTION_CONTRACT.md`,
WRITE → READ-BACK → SPEAK, receipts), the eye lifecycle, presence from the camera, the alarm
ramp on the companion, the browser worker's isolation, the authority boundary. M18.3 extends;
it does not reopen.

## 0. The three owner outcomes

```
A  /core is a full-viewport living gold/amber Core; the small purple wireframe is gone.
B  "90 saniye sonra seçtiğim YouTube müziğiyle test alarmı kur."
   -> schedule -> device armed -> fires without any voice session or focused tab
   -> display wakes -> the named YouTube item really plays -> volume ramps
   -> "Günaydın efendim. Saat ... Alarmınız çalıyor." over ducked music -> restore
   -> "Alarmı kapat." stops it -> every physical step has a receipt -> a test alarm cleans up.
C  Active Eye disabled -> a display test powers the displays off -> a key or the mouse wakes
   them immediately -> the activity is recorded -> stale AWAY/ASLEEP cannot re-darken them.
   Separately: presence-based automatic display-off with conservative sustained thresholds.
```

## 1. Principles that bind every track

1. **Truthful, receipted, owner-governed.** Every physical action (display, media, audio,
   volume) is an `ActionReceipt` with `requested_state`, `execution_status`,
   `terminal_status`, `observed_after`, `evidence_refs`, `error_class`, `speech`, and a
   ledger row. Nothing is spoken as done before its read-back. No hidden action anywhere.
2. **DISPLAY OFF != SYSTEM SLEEP.** Display power is the only machine-state capability. No
   code path in this milestone locks, sleeps, hibernates, logs off, reboots or shuts down.
   The existing source-reading test on the companion stays and is extended to every new
   display file.
3. **Physical owner input outranks passive inference.** Real keyboard/mouse activity wakes
   the display (the OS does this; we never fight it), is recorded, and starts a holdoff
   during which no automatic display-off is issued — enforced on the DEVICE (the companion
   refuses `desktop.display_off` inside its own recent-input window) and in Cloud Core (the
   ambient policy's holdoff). Neither side depends on the other for this.
4. **Uncertain means ON.** `UNKNOWN`, stale, low-confidence, eye disabled, camera failed:
   no automatic display-off. Camera failure never implies sleep. An unwanted screen left ON
   is the accepted failure mode.
5. **The alarm never depends on the display, the camera, the browser, a voice session or a
   network.** Display wake failure → alarm audio still plays. Media failure → the local tone
   with the ramp, and the failure is recorded truthfully. Cloud unreachable → the device's
   own armed fallback rings. Reconnect/restart → no duplicate ring (idempotent by
   `alarm_id` on both sides).
6. **Never bypass.** No CAPTCHA, anti-bot, consent-wall, ad, DRM or autoplay-policy
   circumvention. A challenge is a recorded failure and the fallback tone. Nothing touches
   the owner's own Chrome/YouTube tabs: alarm media lives in the browser worker's dedicated
   `alarm` profile.
7. **The renderer owns no policy.** The UI consumes bus state; policy decisions live in
   Cloud Core (`app/ambient`), execution on the device.
8. **Volume is never the Windows master volume.** The tone ramps its own samples (M18); the
   YouTube volume is the media element's own volume inside the dedicated session; the
   greeting plays at its own level. Nothing is left changed after an alarm.
9. **Same production path in test mode.** A test alarm is a real alarm with `is_test=true`
   and a short offset; a display test issues the real `display.off` receipt.

## 2. State model (cloud-owned)

```
Presence Engine (app/presence, unchanged vocabulary)
   PRESENT | AWAY | RETURNED | AWAKE | RESTING | LIKELY_ASLEEP | UNKNOWN
        │  + a new observation source "input" fed from the device heartbeat status
        ▼
Ambient Policy Engine (app/ambient, NEW; pure decide() + a tick)
        │  inputs: presence assertion, eye enabled, device status (input idle, display
        │  state, alarm ringing), holdoffs, owner policy
        ├── display.off (reason owner_away | owner_likely_asleep)   -> receipt
        └── no_action  (reason recorded only on a test or a change)

Device status (heartbeat `status`, every ~10 s)            ─┐
   input_idle_s, display.state, alarm_ringing, armed_alarms ├─> holdoff + presence + facts
                                                             ┘
Keyboard / mouse ──(OS wakes the display; companion sees input idle reset)──> input holdoff
Wake Alarm ────────────────────────────────────────────────> display.wake + media + greeting
```

Holdoff sources (cloud, `app/ambient/holdoff.py`): `input` (after an input-idle reset that
follows ≥ 5 min of idle, or any input while the display is off), `owner_command` (after a
spoken/REST display or policy command), `alarm_wake` (after an alarm fired), `owner_return`
(after `owner.returned`). Defaults: input 10 min, owner_command 15 min, alarm_wake 30 min,
owner_return 10 min. Configurable in the ambient policy.

## 3. Wake Alarm (Cloud Core, Track C)

### 3.1 Aggregate

Table `wake_alarms` (alembic `20260907_0021_wake_alarms`, ONE head), ORM `WakeAlarm`:

| column | notes |
|---|---|
| `id` | uuid, the `alarm_id` used everywhere (routine action detail, device arm, receipts) |
| `owner_id` | the single owner |
| `device_id` | the device armed for it (nullable until armed; re-selected by capability at fire time if that device is offline) |
| `timezone` | IANA, default the owner's (`Europe/Istanbul`); never UTC for owner-facing times |
| `scheduled_for` | tz-aware instant of the NEXT occurrence (UTC in the DB, shown local) |
| `local_time` | `"HH:MM"` as the owner said it |
| `recurrence` | `null` (one-shot) or `{"weekdays":[0..6]}` |
| `state` | see 3.2 |
| `media_source` | `{"kind":"youtube","url":...,"title":...}` / `{"kind":"tone"}` / `{"kind":"remembered","name":...}` |
| `resolved_media_identity` | `{"kind","url","title","video_id"}` once resolved, else null |
| `volume_policy` | `{"start":0.15,"end":0.6,"ramp_seconds":20}` (validated like `wake_volume`: start < 0.5, start ≤ end) |
| `greeting_policy` | `{"enabled":true,"text":null,"duck_level":0.15}` |
| `display_wake_policy` | `{"enabled":true}` |
| `is_test` | bool |
| `routine_id` | the routine currently carrying the trigger (one-shot `at` or `schedule`) |
| `snooze_count`, `snooze_minutes` | ints |
| `created_at`, `armed_at`, `triggered_at`, `terminal_at` | timestamps |
| `terminal_state`, `terminal_reason` | copied when terminal |
| `last_firing_id`, `media_session_id`, `greeting_due_at`, `greeted_at`, `max_play_seconds` | sequencing state |
| `updated_at` | |

The trigger is a routine (`app/routines`, "extend the proven Routine/Alarm architecture"):
one-shot alarms create an `at` routine, recurring alarms a `schedule` routine, both with a
single action `{"kind":"wake_alarm","detail":{"alarm_id":"<id>"}}` (NEW action kind,
validated: a uuid). A snooze creates a fresh one-shot `at` routine for the same alarm.

### 3.2 Lifecycle

```
SCHEDULED -> ARMED -> FIRING -> DISPLAY_WAKING -> MEDIA_STARTING -> PLAYING -> GREETING -> PLAYING
                 \                                                      \-> SNOOZED -> ARMED ...
                  \                                                      \-> STOPPED (owner)
                   \                                                      \-> COMPLETED (max_play_seconds, media ended)
                    \-> CANCELLED (owner)                     any step -> FAILED (both audio paths failed)
```

Every transition writes exactly one ledger event `alarm.<state_lowercase>` (subsystem
`routine`, `source_ref = alarm_id:state:occurrence`) and publishes the matching bus state
(§7). Transitions are idempotent per `(alarm_id, occurrence)`; the routine engine's
`RoutineFiring` uniqueness remains the first line against double firing.

### 3.3 The clock

`app/routines/clock.py::RoutineClock` — an asyncio loop started in the API lifespan
(`ROUTINE_CLOCK_INTERVAL_S`, default 10 s; `ROUTINE_CLOCK_ENABLED`, default true; unit tests
never start it). Single-flight. Each tick, in a worker thread with its own session:
`routines_service.evaluate_due(now)` → `wake_alarm_service.tick(now)` → `ambient_service.tick(now)`.
Health: `checks.routine_clock = {running, last_tick_at, interval_s, ticks, last_error}`.

This amends M18 row 12.15 ("no background timer"): the routine PACKAGE still has no timer
and `evaluate_due` stays the one explicit entry point; the clock is a separately named,
owner-visible component that asks. The structural test moves to asserting exactly that.

### 3.4 Arming (the device fallback)

On creation, and on every tick while `state in (SCHEDULED, SNOOZED→ARMED pending)` and the
device is online and not yet armed for this occurrence: `desktop.alarm_arm` (§5.2). ARMED
only when the device acknowledged. Cancel/stop/complete → `desktop.alarm_disarm`.

### 3.5 The wake sequence (one firing; each step a receipt)

```
evaluate_due fires the routine -> WakeAlarmDispatcher.dispatch(wake_alarm)
 1. state FIRING; desktop.alarm_disarm {alarm_id, reason:"cloud_firing"}   (idempotent; if
    the device is unreachable the local fallback is exactly what we want)
 2. if display_wake_policy.enabled: desktop.display_wake {reason:"wake_alarm"} -> receipt
    display.wake; failure recorded, sequence continues.  state DISPLAY_WAKING
 3. state MEDIA_STARTING. If resolved media is youtube and the device advertises
    browser.media_play:
      browser.media_play {session_id:"alarm-<alarm_id>", url, volume:start, verify_seconds:3}
      verified -> browser.media_volume {level:end, ramp_seconds} -> receipt media.play
      not verified / challenge / error -> receipt media.play failed (error_class, reason)
    Fallback (media not youtube, capability missing, or media failed):
      desktop.alarm_start {alarm_id, label, wake_volume:{start,end,ramp_seconds},
                           max_duration_s:max_play_seconds} -> receipt alarm.start
    state PLAYING (media_kind youtube | tone_fallback); greeting_due_at = now + ramp_seconds + 2 s
    Both paths failed -> state FAILED, ledger alarm.failed, UiState.ERROR critical.
 4. next ticks: PLAYING and greeting_due_at reached and greeting_policy.enabled:
    state GREETING; if media_kind youtube: browser.media_volume {level:duck_level, ramp_seconds:1}
    synthesize greeting (§3.7) -> desktop.play_audio {audio:{url,sha256,bytes,format:"wav"}, level:0.75, max_seconds:15}
    -> receipt greeting.play; restore: browser.media_volume {level:end, ramp_seconds:1}; state PLAYING, greeted_at
    (tone fallback: the tone keeps ringing; the greeting plays over it at its own level - no duck available)
 5. PLAYING for max_play_seconds (default 600; is_test 120) or browser.media_status ended
    -> stop media/tone -> state COMPLETED; one-shot: routine resolved; is_test: media session
    closed, device disarmed, nothing left armed.
```

Owner stop (tool `alarm.stop`, phrases §6): `browser.media_stop` and/or `desktop.alarm_stop`
→ STOPPED. Owner snooze (`alarm.snooze {minutes}`): stop playback, `scheduled_for += minutes`,
new `at` routine, `desktop.alarm_arm`, state SNOOZED → ARMED. Snooze default 5 min ("Beş
dakika ertele" 5, "On dakika ertele" 10), max 60.

### 3.6 Device status ingestion (heartbeat `status`, §5.3)

`app/devices/status.py::DeviceStatusRegistry` keeps the last status per device (in memory,
observed_at). On each status: (a) `input_idle_s ≤ 120` → a presence observation
`source="input"` (`person_present=true, activity_level="high"|"low", awake_state="awake",
presence_confidence 0.85`), throttled to one per 30 s per device; long idle produces NO
observation (absence of input is not evidence of absence); (b) an input-idle reset after
≥ 300 s idle, or any reset while `display.state == off`, writes ledger `owner.input_active`
and starts the `input` holdoff; (c) `display.state` changes publish `display.on|off` (§7);
(d) `local_alarm_fired` names → the alarm is marked PLAYING (media_kind local_fallback) with
ledger `alarm.local_fallback_rang`; (e) World Model facts `device.display_state`,
`device.input_idle_s`, `alarm.next_scheduled_for` (RUNTIME kind, per device).

### 3.7 Greeting audio (no microphone, no realtime session)

Text: `"Günaydın efendim. Saat <spoken clock>. Alarmınız çalıyor."`; `is_test`:
`"Test alarmınız çalıyor."` instead of the last sentence; before 12:00 "Günaydın efendim",
12:00–17:59 "İyi günler efendim", after "İyi akşamlar efendim". Spoken clock
(`app/alarms/speech.py::spoken_clock_tr`): `07:00` → "yedi", `07:30` → "yedi buçuk",
`07:15` → "yediyi çeyrek geçiyor", `07:45` → "sekize çeyrek var", otherwise "yedi kırk iki"
(numbers via `app.narration.numbers`). Custom `greeting_policy.text` is normalised through
the narration normalizer with the owner's pronunciation map.

Synthesis: `app.voice.providers.OpenAITTSProvider` (the owner's OpenAI key is already the
realtime key) with the voice nearest the realtime persona (`cedar` when the TTS API accepts
it, else the configured fallback); WAV PCM16 mono 24 kHz, ≤ 15 s, cached by text hash in
process. Delivery: `app/alarms/audio_store.py` — one-time token → bytes, 5 min TTL; route
`GET /v1/alarms/audio/{token}` needs NO owner session (the token is the authority, single
use, sha256 in the command payload). The companion fetches over the broker origin only.

### 3.8 Voice tools, intents, speech (§6) — Track C, `tools_ambient.py`, `intents.py`

Tools (all return `ActionReceipt`-shaped results with `speech`; queries return
`speech` + facts):

| tool | class | arguments | notes |
|---|---|---|---|
| `alarm.create` | ACTION | `when: {relative_seconds?, date?("tomorrow"|ISO), time?("HH:MM"), weekdays?[0..6]}`, `when_text?`, `media?: {url?, title?, remembered?}`, `test?: bool`, `label?` | `when_text` parsed deterministically in the owner tz by `app/alarms/tr_time.py`; a title without URL and no remembered match → alarm created with the tone and `speech` asks for the link; recurring + ambiguous media → `needs_media_confirmation` refusal, nothing created |
| `alarm.cancel` | ACTION | `alarm_id?` (default: the next scheduled) | |
| `alarm.snooze` | ACTION | `minutes?` (default 5) | only while PLAYING/GREETING/FIRING |
| `alarm.stop` | ACTION | `alarm_id?` | idempotent |
| `alarm.status` | QUERY | | "Sabah alarmım kaçta?" |
| `display.off` | ACTION | `reason?` | receipt from `desktop.display_off`; a device refusal (`recent_input`, `alarm_active`) is `execution_status=refused` and spoken |
| `display.wake` | ACTION | | |
| `display.status` | QUERY | | |
| `ambient.set_policy` | ACTION | `auto_off?: bool`, `off_when_asleep?`, `off_when_away?`, `wake_on_return?` | persisted (`ambient_policy` table, owner prefs), ledger `ambient.policy_changed` |
| `ambient.test_display` | ACTION | `delay_seconds?` (default 10) | §8.2 |

Intents (`resolve_intent`, the ONE router): `ALARM_CREATE`, `ALARM_CANCEL`, `ALARM_SNOOZE`,
`ALARM_STOP`, `ALARM_QUERY`, `DISPLAY_OFF`, `DISPLAY_WAKE`, `DISPLAY_QUERY`,
`AMBIENT_POLICY_SET`, `AMBIENT_TEST_DISPLAY`, `ALARM_TEST_CREATE` (maps to `alarm.create`
with `test=true`). `CAPABILITY_BY_INTENT` gains the action rows. No second Turkish table
anywhere; the client stays phrase-free.

`ACTION_CONTRACT_VERSION` → **6** (M18.2 released 5). Health `checks.voice_realtime.tools`
lists the new tools; the M18.3 harness gates on the version.

### 3.9 Ambient policy (Track C, `app/ambient/`)

`AmbientPolicy` (owner-editable, persisted): `auto_off_enabled=False` (default until the
owner turns it on by voice or the C qualification does), `off_when_away=True`,
`off_when_asleep=True`, `wake_on_return=True`, `away_after_s=900`, `asleep_after_s=600`,
`asleep_min_confidence=0.7`, `input_holdoff_s=600`, `command_holdoff_s=900`,
`alarm_holdoff_s=1800`, `return_holdoff_s=600`, `quiet_hours=None`.

`decide(inputs, policy, now) -> Decision(action: "display.off"|"none", reason, evidence)`,
pure and exhaustively tested:

```
display already off / unknown state          -> none (display_not_on)
alarm ringing/playing/greeting or armed < 15 min -> none (alarm_context)
any holdoff active                           -> none (holdoff:<source>)
policy.auto_off_enabled false                -> none (policy_disabled)
presence UNKNOWN or stale                    -> none (uncertain)  [never off from uncertainty]
eye disabled                                 -> none (no_perception)  [camera off => no inference]
AWAY held >= away_after_s and off_when_away  -> display.off (owner_away)
LIKELY_ASLEEP held >= asleep_after_s and confidence >= asleep_min_confidence and off_when_asleep
                                             -> display.off (owner_likely_asleep)
otherwise                                    -> none
```

A `display.off` decision runs the action contract (`ActionReceipt` capability `display.off`,
reason `owner_away|owner_likely_asleep`) over `desktop.display_off` and starts NO holdoff
(a refusal from the device — `recent_input` — starts the input holdoff). `wake_on_return`:
`owner.returned` while the display is off → `display.wake` (reason `owner_returned`).

## 4. Browser worker media operations (Track B, `services/browser`, contract v1.2)

Four operations, BROWSER_CAPABILITIES.md §1 rows, `ProtocolConstants.BrowserCapabilities`,
the companion host allowlist, and the cloud allowlist (Track C adds the cloud side):

| op | risk | payload | result |
|---|---|---|---|
| `browser.media_play` | NAVIGATE | `{session_id, url, volume: 0..1, verify_seconds: int(1..10)}` | `{playing, verified, url, final_url, title, current_time_s, duration_s|null, volume, muted, reason: null|"no_media_element"|"autoplay_blocked"|"challenge"|"consent_wall"|"navigation_failed"|"error"}` |
| `browser.media_volume` | NAVIGATE | `{session_id, level: 0..1, ramp_seconds: 0..120}` | `{applied, level_from, level_to, ramp_seconds}` — the ramp runs inside the page (interval), the op returns once started (or after completion when ramp ≤ 2 s) |
| `browser.media_status` | READ | `{session_id}` | `{present, playing, paused, ended, current_time_s, duration_s|null, volume, muted, title, url}` |
| `browser.media_stop` | NAVIGATE | `{session_id}` | `{stopped, was_playing}` — pauses, then closes the session |

Rules: the session is opened by the ordinary `browser.session_open` with
`profile: "alarm"` — a NEW dedicated persistent profile directory (never the research
profile, never the owner's Chrome) — and `session_kind: "media"`, under which the worker
launches Chrome with `--autoplay-policy=no-user-gesture-required` (a browser preference for
our own dedicated window; documented as not an anti-bot measure) and a visible window.
`media_play` navigates (same destination policy), waits ≤ 10 s for a `<video>` element,
sets `volume` FIRST, calls `play()`, then verifies `currentTime` advanced ≥ 0.5 s over
`verify_seconds` with `paused=false`. A CAPTCHA / "confirm you're not a bot" / sign-in wall /
consent wall the page cannot leave → `reason` set, `verified=false`, NO retry, NO bypass, no
click on anything. Nothing here reads cookies or storage (the forbidden-key scan stays).
Tests: the fake `Page` style already used by the worker suite; no browser is ever launched.

## 5. Windows agent (Track D, `devices/windows-agent`)

### 5.1 Display

- `desktop.display_wake` `{reason}` → `{display_wake_requested: true, methods: [...],
  observed_state: "on"|"off"|"dimmed"|"unknown", observed_at}`. Implementation:
  `SetThreadExecutionState(ES_DISPLAY_REQUIRED)` (momentary, no `ES_CONTINUOUS`) plus a
  zero-delta synthetic mouse move (`SendInput`, `MOUSEEVENTF_MOVE` dx=dy=0), then the
  display state read back from the power-setting notifications below. Never a key press.
- `desktop.display_status` `{}` → `{state, observed_at, source: "power_notification"|
  "unknown", input_idle_s, holdoff_until|null}`.
- Display state observation: a message-only window registers
  `RegisterPowerSettingNotification(GUID_CONSOLE_DISPLAY_STATE)` (0 off / 1 on / 2 dimmed)
  and `GUID_MONITOR_POWER_ON`; the last value and its time are the observed state.
- `desktop.display_off` keeps its name and gates (`DisplayPowerEnabled` on service and
  companion). New payload fields `reason`, `holdoff_s?` (default 120). The companion REFUSES
  inside its own recent-input window or while an alarm rings, as a SUCCESSFUL command with
  `{display_off: false, refused: "recent_input"|"alarm_active", input_idle_s, holdoff_s}`
  (the cloud maps it to `execution_status=refused`). The result always carries the
  observed state after the request. The existing source-reading guard test covers every
  display file (`DisplayPowerController.cs`, the new observer, the wake implementation):
  no shutdown/suspend/hibernate/logoff/lock API name may appear.
- Multi-monitor: `SC_MONITORPOWER` and the wake apply to the display set as Windows
  presents it; the status reports `monitors: n` from `EnumDisplayMonitors` when available.
  No topology, resolution, orientation or primary changes anywhere.

### 5.2 Local alarm arming (the network-loss fallback)

- `desktop.alarm_arm` `{alarm_id, fire_at (ISO-8601 UTC), grace_s (5..300, default 45),
  fallback: {label?, wake_volume{start,end,ramp_seconds}, max_duration_s}}` →
  `{armed: true, alarm_id, fire_at, fires_locally_at}`. Persisted at
  `%LOCALAPPDATA%\PagentOS\companion\armed-alarms.json` (atomic write). A local timer rings
  the fallback tone (the existing `AlarmController.Start` path, same ramp rules) at
  `fire_at + grace_s` UNLESS a cloud `desktop.alarm_start`/`alarm_disarm` naming that
  `alarm_id` arrived first. A cloud `alarm_start` with the id consumes the arm. On companion
  start, arms are reloaded; an overdue unconsumed arm younger than 2 h rings at once,
  older ones expire with an audit line. Every arm/fire/consume/expire is audited.
- `desktop.alarm_disarm` `{alarm_id, reason?}` → `{disarmed, was_armed}`. Idempotent.
- `desktop.alarm_start`/`desktop.alarm_stop` unchanged (a start's `alarm_id` consumes an arm).

### 5.3 Activity status and the heartbeat

- `desktop.activity_status` `{}` → `{input_idle_s, last_input_at, display: {state,
  observed_at}, alarm_ringing, ringing_alarm_id|null, armed_alarms: [ids],
  local_alarm_fired: [ids since last report], holdoff_until|null, observed_at}`.
  `input_idle_s` from `GetLastInputInfo` (a tick count; no key or pointer content is ever
  read or stored).
- The Device Service asks the companion for it before each heartbeat and sends
  `{"type":"heartbeat","seq":n,"status":{...}}`; `status` is OPTIONAL in the schema
  (protocol version stays 1, additive). No companion → no `status`.

### 5.4 Greeting playback

- `desktop.play_audio` `{audio_id, audio: {url, sha256, bytes (≤ 2 MiB), format: "wav"},
  level: 0..1 (default 0.75), max_seconds (≤ 20)}` → `{played: true, audio_id, duration_ms,
  level}`; blocks until playback ends (inside the 60 s cap). The service validates the URL
  origin against its configured broker REST origin before forwarding; the companion fetches
  (10 s timeout), verifies sha256 and size, decodes PCM16 WAV, and plays through the same
  render-endpoint path as the tone (`IAudioDeviceFactory`), scaling samples by `level`.
  Never the master volume. Audited.

### 5.5 Advertisement and install

`AgentCapabilities.Compose` adds `desktop.display_wake`, `desktop.display_status`,
`desktop.activity_status`, `desktop.alarm_arm`, `desktop.alarm_disarm`, `desktop.play_audio`
unconditionally (they are safe by construction); `desktop.display_off` stays behind
`DisplayPowerEnabled`. `scripts/install-device-service.ps1` gains `-DisplayPower`
(writes `DisplayPowerEnabled=true` to BOTH the service and the companion configuration)
so the M18.3 install turns it on in one owner-authorised step. The existing update path
(re-running the installer over an install) is what the owner harness prints.

## 6. Owner phrases (Turkish, all through `resolve_intent`)

```
Yarın sabah 07:30'da beni uyandır.                        ALARM_CREATE
Yarın 07:30'da YouTube'dan Hans Zimmer Time ile beni uyandır.   ALARM_CREATE (media title)
Her hafta içi 07:15'te beni bu şarkıyla uyandır.          ALARM_CREATE (recurring, remembered)
Saat 08:00'e alarm kur.                                    ALARM_CREATE
Sabah alarmım kaçta?                                       ALARM_QUERY
Alarmı iptal et.                                           ALARM_CANCEL
Alarmı kapat.   / Alarmı durdur. / Alarmı sustur.          ALARM_STOP
Beş dakika ertele. / On dakika ertele.                     ALARM_SNOOZE
90 saniye sonra test alarmı kur.                           ALARM_TEST_CREATE
90 saniye sonra YouTube'dan <song> ile test alarmı kur.    ALARM_TEST_CREATE (media)
Ekranları kapat. / Ekranı kapat.                           DISPLAY_OFF
Ekranları aç. / Ekranı aç.                                 DISPLAY_WAKE
Uyurken ekranları kapat.                                   AMBIENT_POLICY_SET off_when_asleep
Ben yokken ekranları kapat.                                AMBIENT_POLICY_SET off_when_away
Otomatik ekran kapatmayı kapat. / ... aç.                  AMBIENT_POLICY_SET auto_off
Ben geri geldiğimde ekranı aç.                             AMBIENT_POLICY_SET wake_on_return
Ekran uyku otomasyonunu test et.                           AMBIENT_TEST_DISPLAY
```

Speech table (receipt `speech`, `docs/M18_ACTION_CONTRACT.md` §5 style): "Alarmı yarın
yedi otuza kurdum efendim." / "Test alarmını doksan saniye sonraya kurdum efendim." /
"Alarmı iptal ettim efendim." / "Alarmı kapattım efendim." / "Beş dakika erteledim efendim;
yedi otuz beşte tekrar çalacak." / "Sabah alarmınız yedi otuzda efendim." / "Kurulu alarm
yok efendim." / "Ekranları kapattım efendim." / "Ekranları açtım efendim." / "Ekranlar zaten
açık efendim." / "Ekranı kapatmadım efendim; az önce klavye kullanıldı." / "Otomatik ekran
kapatmayı açtım efendim." / "Ekran testini başlattım efendim; on saniye sonra ekranlar
kapanacak, bir tuşa basınca açılacak." No banned completion phrase, ever.

## 7. UI state contract v3 (bus; server `contract.py` and web `contract.ts` together)

New states (`CONTRACT_VERSION` 2 → 3 on both sides):

| state | subsystem | ttl | drawn as |
|---|---|---|---|
| `alarm.armed` | routine | 12 h | ambient strip only ("Alarm 07:30") |
| `alarm.firing` | routine | 120 s | the wake surge begins (nucleus brightens, rings accelerate) |
| `alarm.playing` | routine | 20 min | sustained wake surge, `intensity` = the current ramp level when published |
| `alarm.greeting` | routine | 60 s | speaking-like pulse without a voice session |
| `alarm.snoozed` / `alarm.stopped` / `alarm.completed` / `alarm.failed` | routine | 5 min | the surge releases; failed is `error` severity |
| `display.on` / `display.off` | ambient | 24 h | a display channel on the ambient strip (never the Core) |

Channel rule (ADR-0056/0065): `alarm.*` never displaces a genuinely thinking/speaking Core;
it is its own channel like release. `display.*` is ambient-band only. Existing states and
their visuals are unchanged.

## 8. Test mode (production path, bounded)

### 8.1 Test alarm

`alarm.create {test: true, when: {relative_seconds: 90}, media: ...}` — the same table, the
same routine, the same arm, the same sequence; `max_play_seconds=120`; on any terminal
state the media session is closed, the device disarmed, the one-shot routine resolved, and
a ledger `alarm.cleaned_up` written. Nothing special-cased in the sequence.

### 8.2 Display input-wake test

`ambient.test_display {delay_seconds}` (default 10; "Ekran uyku otomasyonunu test et."):
`speech` announces it; the clock issues the real `display.off` receipt (reason
`owner_test`) at T+delay; the companion's power notification reports `off`; the owner
presses a key or moves the mouse; the OS wakes the display; the next heartbeat status shows
`input_idle_s` reset and `display.state=on`; Cloud Core writes `owner.input_active`, starts
the input holdoff, publishes `display.on`; the harness asserts no `display.off` receipt
follows within the holdoff. It runs with the eye DISABLED (the harness disables it first
through the proven `eye.disable` path) to prove camera failure cannot lock the owner out.

## 9. Living Core (Track W, `apps/web`)

- Minimal `/core`: the stage is the viewport (`100dvh`, no page scroll); the Core covers
  60–80 % of the usable viewport by aspect (`stageSizeFor(w, h)`, pure, tested); a
  near-black ground (`#06050a`); overlays only — a tiny semantic caption (bottom centre), a
  connection dot, a control cluster (voice, eye, quality tier, 2D, fullscreen, cockpit link)
  that fades to 25 % after 4 s without pointer/focus and returns on either, an ambient
  strip (presence, eye privacy cell at full contrast always, display, alarm, release).
  Fullscreen through `requestFullscreen()` on the owner's gesture with a visible exit and
  Esc; a PWA manifest (`app/manifest.ts`, `display: "standalone"`, dark theme) — no
  user-gesture restriction is bypassed.
- Gold/amber identity: nucleus warm white → gold (`#fff1d0` → `#ffc86a`), energy amber
  (`#f0a53a`), ember for error (`#e0623c`), the eye aperture keeps its cool privacy colour.
  Layers, outermost first: outer field, containment shell, topology shell, orbital layers
  (independent rings, differing rates and directions), data/circuit layer (procedural
  paths), particle transport (instanced, bounded), processor structures (floating
  structural fragments), energy chamber, central nucleus. Parallax and restrained
  additive halos; no post-processing library.
- Real state drives it (unchanged rule): idle breathing only; LISTENING inward pull;
  OWNER_SPEAKING from the measured mic level; THINKING topology activation; TOOL_RUNNING
  peripheral structures; RESEARCHING the bounded constellation; MEMORY convergence;
  SPEAKING semantic (ADR-0066) with RMS only on glow/pulse/deformation/particle velocity/
  nucleus amplitude; EYE_ACTIVE aperture; WAITING_OWNER suspended; SHADOW_READY nodes;
  NEW `alarm.*` wake surge and `display.*` strip.
- Tiers HIGH/BALANCED/LOW with `sceneBudgetFor` re-derived; hidden tab draws nothing;
  reduced motion is still; WebGL absent → the 2D fallback in the same identity.
- Cockpit (`/core/cockpit`) keeps a reduced Core and adds panels: Alarms (`/v1/alarms`),
  Ambient/Display (`/v1/ambient/policy`, device status), rendering "henüz yok" until the
  routes exist. Nothing in the renderer decides policy.

## 10. Tracks, ownership, numbers

| track | agent | owns | must not touch |
|---|---|---|---|
| C — Cloud Core | backend-engineer | `services/api/app/{alarms,ambient,routines,devices,presence,uistate,voice,actions,ledger,worldmodel,health,main,config}`, migrations, API tests, ADR-0071 | `apps/web`, `devices/`, `services/browser`, BUILD_STATE, QUALIFICATION, ACCEPTANCE, OWNER_ACTIONS |
| D — Windows agent | windows-engineer | `devices/windows-agent/**` (except `BrowserCapabilities` constants), `packages/protocol/DEVICE_PROTOCOL.md` §6c–§6h, `packages/schemas/device-protocol.schema.json`, `scripts/install-device-service.ps1`, ADR-0072 | cloud code, web, the browser worker |
| B — Browser media | browser-engineer | `services/browser/**`, `packages/protocol/BROWSER_CAPABILITIES.md`, `ProtocolConstants.BrowserCapabilities` + the companion host allowlist + `BrowserDispatchTests`, ADR-0073 | cloud dispatch allowlist (Track C), display/alarm code |
| W — Living Core | general-purpose (frontend) | `apps/web/**`, `docs/M18_3_LIVING_CORE_VISUAL_IDENTITY.md`, ADR-0070 | anything server-side |

ADR-0068 is the M18.2 research fast path (in flight). ADR-0069 is this architecture.
Integration, the owner harnesses (`scripts/core/owner-m18-3-*.ps1`), QUALIFICATION Stage 14,
ACCEPTANCE_TESTS, OWNER_ACTIONS and BUILD_STATE are the integrator's.

## 11. Automated acceptance before any owner test

- full-viewport renderer adapts (stage size 60–80 % across aspect ratios); tiers bounded
  (`sceneBudgetFor` vs mounted objects); hidden page renders nothing; Voice/Eye suites
  unchanged and green; SPEAKING lifecycle tests unchanged; single realtime session tests
  unchanged;
- alarm survives restart (a SCHEDULED/ARMED row fires after a fresh process's first tick);
  no duplicate firing (two ticks, two processes, a reconnect: one firing, one ring);
  device arm/consume/expire on a manual clock; overdue arm rings once after restart;
- YouTube failure (challenge, no media element, autoplay blocked) → tone fallback with the
  failure receipt; volume never touches a system mixer (structural: no CoreAudio volume API
  in the companion, asserted by reading the source);
- greeting needs no microphone and no realtime session (the path never touches
  `RealtimeSessionRow`); the greeting plays with the tone fallback too;
- display-off never suspends (source guard on every display file); camera failure / eye
  disabled / UNKNOWN / stale → `decide()` never returns `display.off`; a holdoff blocks it;
  a device `recent_input` refusal is a refused receipt and starts the input holdoff;
  alarm-time display wake failure does not stop the alarm audio; display wake works with
  the eye disabled (no presence dependency in the sequence);
- no raw camera archive (unchanged guards); every physical action has a receipt (structural
  test: the sequence's device calls are enumerated and each maps to a receipt capability).

## 12. Owner qualifications (short, three, in order)

- **A — Visual**: `scripts/core/owner-m18-3-core.ps1` starts the web shell, verifies the
  served build is the Living Core, and opens `/core`; the owner reviews scale, depth,
  gold/amber, speaking, listening, eye, research constellation.
- **B — Wake alarm**: `scripts/core/owner-m18-3-alarm.ps1` — preflight (Cloud Core release
  when the contract version is stale; the agent update command when the device manifest
  lacks the new capabilities — elevation is the owner's), then the owner says
  `90 saniye sonra seçtiğim YouTube müziğiyle test alarmı kur.` (the harness accepts a URL
  parameter for the music), and the harness proves from the record: created → armed →
  fired at the scheduled instant → display.wake receipt → media.play verified (or the
  truthful fallback) → ramp → greeting receipt → duck/restore → `Alarmı kapat.` → stopped →
  cleaned up.
- **C — Ambient display**: `scripts/core/owner-m18-3-display.ps1` — eye disabled → test
  display-off → the owner touches a key/mouse → display on within seconds → input activity
  recorded → holdoff → no re-off. Then, when the owner wants it, the presence-based
  automatic off with the conservative thresholds (a separate longer real observation).
