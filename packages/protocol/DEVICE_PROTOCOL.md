# Device Protocol v1

Contract between the cloud Device Broker and device agents (first: Windows Device Agent). Both sides validate messages against `packages/schemas/device-protocol.schema.json`. Protocol version is negotiated in `hello`; incompatible versions are rejected at handshake.

## 1. Transport

- WebSocket, **always initiated outbound by the device agent**. No inbound port is ever opened on the device.
- JSON text frames, UTF-8, one protocol message per frame.
- The transport endpoint is configuration, not code: dev `ws://127.0.0.1:8001/v1/devices/connect`; production later `wss://…` over Tailscale (private overlay) without agent code changes. TLS/mTLS termination is a deployment concern below the protocol layer.
- The agent treats any socket failure identically: reconnect with exponential backoff 1 s → 60 s (factor 2, full jitter), forever.

## 2. Identity and enrollment

- Each device generates an **ECDSA P-256 keypair** at first run; the private key never leaves the device (stored under the device account with OS file ACLs; DPAPI hardening is a later milestone).
- Enrollment (REST, one-time):
  1. Owner/operator obtains a one-time enrollment token: `POST /v1/devices/enrollment-tokens` → `{token, expires_at}`. Since M9 this requires an owner bearer session (`Authorization: Bearer <token>`, ADR-0027) **and** a loopback peer.
  2. Agent calls `POST /v1/devices/enroll` with `{token, name, platform, public_key_spki_b64, capabilities}` → `{device_id}`. This is the one broker REST endpoint that does NOT require an owner session: the enrollment token *is* its credential, and the enrolling agent has no owner session by construction. The trust chain still roots in the owner, because only an authenticated owner can mint that token.
  - Tokens are single-use, short-lived (default 15 min), stored hashed.
- In protocol v1, every enrollment creates a **new device identity** (the enroll request carries no device_id, so the broker cannot safely bind it to an existing row). Key rotation for an existing device is deferred to a dedicated authenticated rotate endpoint in a later protocol revision. Revocation: broker marks device revoked; all WS sessions close; the handshake is rejected thereafter; and since M9 every owner session bound to that `device_id` is revoked too, so the device's bearer token stops working on the REST API as well (M9 acceptance: *device revocation invalidates session*).
- Signature encoding: the broker accepts both DER and raw IEEE P1363 `r||s` (64-byte) ECDSA signatures.

## 3. Handshake (over WS)

```
agent  -> {"type":"hello","protocol_version":1,"device_id":"…","software_version":"…","capabilities":["desktop.open_application"]}
broker -> {"type":"challenge","nonce":"<b64 32 bytes>"}
agent  -> {"type":"auth","signature":"<b64 ECDSA-SHA256 over nonce_bytes||device_id_utf8>"}
broker -> {"type":"welcome","session_id":"…","heartbeat_interval_s":10}
```

Failure at any step: broker sends `{"type":"error","error":{"class":"auth_error",…}}` and closes. Unknown/revoked device or bad signature never reveals which check failed.

## 4. Heartbeat and presence

- Agent sends `{"type":"heartbeat","seq":n}` every `heartbeat_interval_s`; broker replies `{"type":"heartbeat_ack","seq":n}`.
- Broker marks the device **offline** when no frame arrives for `2.5 × heartbeat_interval_s`, and on socket close. Presence is observable at `GET /v1/devices`.
- Any frame refreshes liveness; heartbeats are only a floor.
- Since M18.3 a heartbeat MAY carry an optional `status` object (§6g). It is additive, `protocol_version` stays 1, and nothing about presence depends on it: a heartbeat without one is the frame v1 always sent.

## 5. Commands

Broker → agent:

```json
{"type":"command","command":{
  "command_id":"uuid","idempotency_key":"uuid",
  "capability":"desktop.open_application",
  "payload":{"application":"notepad"},
  "expires_at":"2026-08-31T12:00:00Z","trace_id":"…"}}
```

Agent → broker acknowledgements (`command_ack`), monotonic per command:

`accepted` → `running` → terminal `succeeded` | `failed`

```json
{"type":"command_ack","command_id":"…","status":"succeeded",
 "result":{"pid":1234},"error":null}
```

`failed` carries `error: {class, message, retryable}` using the taxonomy of `docs/API_AND_PROTOCOLS.md` (plus `command_expired`, `cancelled`).

Rules:
- **Expiry:** agent rejects a command whose `expires_at` has passed with `command_expired` (never executes). Broker also expires undelivered commands server-side.
- **Idempotency:** agent keeps a persistent map `idempotency_key → terminal ack` (bounded LRU, survives restarts). A duplicate delivery re-sends the recorded terminal ack and never re-executes. Broker dedups REST creation on `idempotency_key` as well. Delivery is therefore at-least-once, execution effectively-once.
- **Cancellation:** broker sends `{"type":"cancel","command_id":"…"}`. If not yet terminal, agent aborts and acks `failed` with `error.class="cancelled"`; if already terminal, it re-sends the terminal ack. A cancel for a command the agent has never seen is acked `failed`/`cancelled` so the broker can settle it (the agent records nothing, having no idempotency key for it).
- **In-flight duplicate delivery:** a duplicate of a command that is still executing is answered with a `running` re-ack and never re-executed (monotonicity preserved).
- **Malformed frames:** receiver answers `{"type":"error","error":{"class":"validation_error",…}}` referencing `command_id` when parseable; the connection stays open.
- **Redelivery:** on (re)connect the broker re-delivers all non-terminal, non-expired commands for that device.

## 5a. Sideband push: `voice_sideband` (M12, ADR-0039)

Broker → agent, additive to v1. Cloud Core's realtime-voice session service delivers
sideband messages for the owner-session companion (`say`, `tool_progress`,
`tool_completed`, `plan_changed`, `narration_cursor`, `leg_closed`) over the SAME
outbound-only device connection, as their own frame type:

```json
{"type":"voice_sideband","session_id":"uuid","event":"tool_completed",
 "payload":{"call_id":"call_1","status":"succeeded","result":{}},
 "at":"2026-09-02T12:00:00Z"}
```

Rules:
- **Opaque to the Device Service.** The service validates the envelope (uuid session id,
  event name, object payload, ≤ 16 KiB serialized) and forwards the frame unchanged over
  the authenticated local IPC (`voice_sideband` pipe frame, carrying the connection id and
  sequence every pipe frame carries). It never interprets the payload, never acts on it,
  and never replies to the broker for it.
- **Nothing from the command path applies:** no `command_ack`, no idempotency key, no
  expiry, no audit row per frame. A frame that cannot be forwarded (no companion
  connected, oversize) is dropped by the agent; Cloud Core already queues undelivered
  pushes on the session record and the client receives the backlog with its next
  `POST /v1/voice/realtime/sessions/{id}/events` response or on `attach`.
- **Older receivers:** an agent that predates this frame answers it with the standard
  `validation_error` error frame (§5, malformed frames); the broker logs that and the
  session continues. Newer agents with no companion connected simply drop it.

## 6. Capability: `desktop.open_application` (M1)

Payload: `{"application":"<name>","args":[…]?}`. The agent maintains a local **allowlist** (M1 default: `notepad`, `calc`); anything else fails with `capability_missing`. The application must start in the **interactive owner session** (executed by the session companion, not the background service). Result: `{"pid":<int>,"executable":"<path>"}`.

## 6a. Capability: `desktop.open_artifact` (M3)

Payload: `{"path":"<absolute local file path>","artifact_id":"<uuid>"?}`. The session companion opens the file with its OS-associated application (ShellExecute, never launching a program directly) and returns `{"opened":true,"path":"<canonical path>","handler":"shell-associated"}`. Interactive-session capability, executed by the companion.

Allowlist (checked in order): reject non-absolute/blank/UNC paths (`validation_error`); hard-deny executable extensions even inside a root (`security_scope_error`); require a document extension in the configurable allowlist, default `.pdf .docx .html .htm .txt .md` (`capability_missing`); require the canonical resolved path (symlinks/`..` collapsed) to lie under a configured artifact root, default `%LOCALAPPDATA%\PagentOS\agent\artifacts` plus configured extra roots (`security_scope_error`); require the file to exist (`dependency_unavailable`). Every attempt is written to the companion's local JSONL audit with path, artifact_id and result.

## 6b. Capability family: `browser.*` (M13)

The browser family rides this protocol unchanged — one command envelope, one
`accepted → running → succeeded|failed` lifecycle, one idempotency store, one audit log.
Names, payloads, results, sessions, risk classes and the companion ↔ worker stdio protocol
are specified in **`packages/protocol/BROWSER_CAPABILITIES.md`** (ADR-0050), which is the
binding contract; this section only states how the family is carried on the device side.

- **Advertisement.** The family marker `browser.chrome` and the per-operation names
  (`browser.session_open` … `browser.fetch_evidence`) appear in `hello.capabilities` and in
  the enrollment manifest **only when the device has a configured Browser Worker**:
  service option `BrowserEnabled=true` (written by the installer after the worker's
  self-check passed) and, on the companion, `BrowserWorkerCommand` set. The two desktop
  names are advertised unconditionally, exactly as before. A device never advertises a
  family it cannot execute; Cloud Core selects devices by capability (§8 of the browser
  contract), so an absent name means `no_capable_device`, not a hang.
- **Routing.** The Device Service (Session 0) forwards every `browser.*` command over the
  authenticated companion pipe like `desktop.*`; the Session Companion (owner session)
  hands it to the Browser Worker child process it owns. With `BrowserEnabled=false` the
  service answers `capability_missing` (not retryable) before consulting the companion.
  With no companion connected the answer is `dependency_unavailable` (retryable), as for
  the desktop family. A companion without a configured worker answers `capability_missing`.
  Unknown names in the `browser.` namespace, and the marker `browser.chrome` itself, are
  `capability_missing`.
- **Timeout cap.** The service caps the time one command may hold the companion per family:
  **60 s for `desktop.*`** (unchanged), **120 s for `browser.*`**. The pipe request carries
  `timeout_ms` (the remaining command life clamped to [1 s, cap]) and, since 2026-09-04,
  `deadline_utc_ms` (the absolute Unix-ms moment the service gives up; optional, 0 when
  absent); the companion forwards what is LEFT of that to the worker (less a 500 ms headroom
  so the typed `timeout` arrives before the service synthesises one - measured from the
  deadline, so pipe latency is not charged against the headroom), cancels the worker's
  request when it elapses, and answers `timeout` (retryable).
- **Concurrency.** Browser requests execute concurrently on the companion and their
  responses may interleave on the pipe; `request_id` correlates them, every frame still
  carries the connection id and a strictly increasing sequence (ADR-0028), and one
  `exec_response` answers one `exec_request`. Desktop requests are unaffected.
- **Companion-side checks.** Before a worker result crosses the pipe the companion enforces
  the contract's result cap (48 KiB → `internal_bug`; the worker must truncate) and the
  forbidden-key rule (`cookie`, `authorization`, `set-cookie`, `localstorage`,
  `sessionstorage`, `password`, `token`, `secret`, `apikey` — case-insensitive substring of
  any key at any depth → `security_scope_error`). Error classes outside §8's taxonomy are
  reported as `internal_bug`; `browser_lifecycle_violation` (the worker found, or left, the
  PagentOS profile's Chrome outside its own lifecycle — an orphan holding the profile lock)
  passes through as itself and is **never retryable**, whatever the worker said. The
  worker's stderr goes to the companion log, never into a result.
- **Worker lifecycle.** Started lazily on the first `browser.*` request (or eagerly with
  `BrowserWorkerEager=true`), one at a time; restarted with exponential backoff after an
  exit, with in-flight requests failed `dependency_unavailable` (retryable) immediately;
  pinged for liveness; told `shutdown` and then killed (process tree) when the companion
  stops. **Orphan Chrome (2026-09-03 incident):** whenever the companion kills the worker
  (no hello, missed pings, oversize stdout line, ignored shutdown) or observes it exit, and
  before every worker start, it terminates every `chrome.exe` main process whose command
  line carries `--user-data-dir=<BrowserProfileDir>` — that profile only, never another
  Chrome — logs the pids, and counts them (`OrphanChromesReaped`). The installer's
  `Stop-AgentRuntime` does the same after stopping the worker.
- **Companion log.** The companion logs to its console and to
  `<companion DataDir>\logs\companion.log` (the service's JSONL format, size-bounded and
  rotated): every worker start / exit / kill with its reason, the sanitised worker stderr,
  and one line per browser request (capability, `request_id`, outcome, `duration_ms`).
- **Audit.** The companion writes one `browser_request` row per request — capability,
  `request_id`, outcome class, `duration_ms`, never payload or result text, never a URL —
  plus `browser_worker_started` / `browser_worker_exited` / `browser_chrome_reaped` rows;
  the service writes its usual per-command rows.

Configuration (all `PAGENTOS_AGENT_`-prefixed, nothing machine-specific): service
`BrowserEnabled` (default false); companion `BrowserWorkerCommand`, `BrowserWorkerArgs`,
`BrowserDataDir` (default `<companion DataDir>\browser`), `BrowserProfileDir` (default
`<BrowserDataDir>\profile`), `BrowserChannel` (default `chrome`), `BrowserVisible` (default
true), `BrowserIdleTimeoutS` (default 600), `BrowserWorkerEager` (default false).

## 6c. Capability pair: `desktop.alarm_start` / `desktop.alarm_stop` (M18)

The wake alarm. **Interactive-session capabilities, executed by the Session Companion**: they open a shared-mode render endpoint in the owner's session, which the Session-0 Device Service cannot do and never attempts. The service routes them over the companion pipe like `desktop.open_*`, under the same 60 s per-command cap — `alarm_start` arms a ramp and returns, it does not hold the pipe while the alarm rings.

Both names are advertised **unconditionally**. A companion with no render endpoint answers `dependency_unavailable` (retryable) — a true statement about right now, not a claim that the device cannot ring — and a companion built without an alarm controller answers `capability_missing`. Neither ever hangs.

`desktop.alarm_start`

Payload: `{"alarm_id":"<id>"?, "label":"<short text>"?, "wake_volume":{"start":<0..1>,"end":<0..1>,"ramp_seconds":<int>}?, "max_duration_s":<int>?}`
Result: `{"started":true,"alarm_id":"…","start_volume":<f>,"end_volume":<f>,"ramp_seconds":<i>,"max_duration_s":<i>,"end_volume_clamped":<bool>,"ramp_seconds_clamped":<bool>,"replaced_alarm_id":"…"|null}`

`desktop.alarm_stop`

Payload: `{"alarm_id":"<id>"?}` — omit it to stop whatever is ringing.
Result: `{"stopped":<bool>,"alarm_id":"…"|null,"was_ringing":<bool>}`

Rules, all enforced on the device regardless of what Cloud Core validated (a routine row written before a validator existed must not ring at full volume today):

- **A wake volume is a RAMP, never a level.** `start` defaults to 0.05, `end` to 0.8, `ramp_seconds` to 60. A `start` at or above **0.5** is refused with `validation_error` — a caller asking to begin at 0.9 is not asking for a ramp, and quietly lowering it would hide a malformed request. `start > end` is refused for the same reason.
- **There is a ceiling, and clamps are reported.** An `end` above **0.85** is clamped to it and `end_volume_clamped` says so; a `ramp_seconds` below 5 (or above 3600) is clamped into range and `ramp_seconds_clamped` says so. Clamping rather than refusing here keeps the alarm ringing at a safe level instead of not ringing at all, and the result carries the number it will actually reach.
- **The level scales the companion's own generated samples.** Nothing in this capability touches the Windows master volume or the endpoint's volume: an alarm that raised the system mixer would leave the machine loud after it stopped, and would do it to every other application at once.
- **It always stops.** Three ways: `desktop.alarm_stop`, the alarm's own `max_duration_s` (default 300 s, clamped to 10–1800 s, checked continuously), and companion shutdown. No ringing alarm outlives the companion process.
- **`alarm_stop` is idempotent.** Stopping an alarm that already stopped is a success with `was_ringing:false`. A stop naming a *different* alarm than the one ringing does nothing and reports `stopped:false`, so a stale retry cannot silence the alarm that replaced it.
- **One alarm at a time.** A second `alarm_start` stops the first and names it in `replaced_alarm_id` rather than layering two ramps on one endpoint.
- **Since M18.3, both names consume a local arm** (§6f) carrying the same `alarm_id`: a successful `alarm_start` because the cloud got there in time, and a successful `alarm_stop` because the owner has already dealt with it. That is what makes "the fallback never doubles the cloud's alarm" true rather than likely.

Configuration (companion, `PAGENTOS_AGENT_`-prefixed): `AlarmRenderDevice` (id or name substring; falls back to `VoiceRenderDevice`, then the session's default render endpoint, resolved per alarm so a headset plugged in after startup is usable).

## 6d. Capability: `desktop.display_off` — the invariants (M18)

Turns the owner's display off, and nothing else. **Interactive-session capability, executed by the Session Companion** — a `WM_SYSCOMMAND` / `SC_MONITORPOWER` broadcast from Session 0 reaches no window the owner can see. Sent with `SendMessageTimeout` (2 s, `SMTO_ABORTIFHUNG`), because a single hung top-level window would otherwise stall the companion forever; a timed-out broadcast is reported as `dependency_unavailable`, never as success.

> **Payload, result and the third gate are in §6e** (M18.3). This section keeps the invariants,
> which have not changed; §6e adds `holdoff_s`, the refusal shape, the observed state and the
> monitor list.

- **Off is the only operation.** M18 v1 does not shut down, reboot, hibernate, suspend or log off on any inference (`docs/M18_HOLOGRAPHIC_CORE_SPEC.md` §4, `docs/M18_THREAT_MODEL.md` §5). Turning a display off is undone by moving the mouse; suspending a machine that is mid-research is not, and the background work would stop with it. A test reads the implementation source and fails if a shutdown/suspend API name appears in it, so the capability cannot quietly grow a second meaning.
- **Two independent gates, and neither knows about the other.** On the device, the name is advertised and routed only when `DisplayPowerEnabled` is set (default false) — service and companion each refuse it otherwise with `capability_missing`, so a command aimed straight at the device still cannot blank the screen. In Cloud Core, a routine's `display_action` is refused before it ever becomes a command, until display-off has passed its own owner qualification (`docs/M18_HOLOGRAPHIC_CORE_SPEC.md` §7). A single flag flipped by accident is therefore not enough to interrupt unrelated owner work.
- A device with the flag off does not advertise the name, so Cloud Core's capability selection reports `no_capable_device` rather than reaching a device that lied about what it can do.

Configuration: `PAGENTOS_AGENT_DisplayPowerEnabled` on both the Device Service (routing + advertisement) and the Session Companion (execution + advertisement).

## 6e. Capability family: `desktop.display_wake` / `desktop.display_status` / `desktop.display_off` (M18.3)

The display family. All three are **interactive-session capabilities executed by the Session Companion**, under the desktop family's 60 s per-command cap. `display_wake` and `display_status` are advertised **unconditionally**; `display_off` keeps both gates of §6d and gains a third that is not a flag.

### `desktop.display_wake`

Payload: `{"reason":"<short token>"?}`
Result: `{"woken":true,"method":"display_needed+pointer_move_zero","observed_state":"unknown"|"off"|"on"|"dimmed","observed_at":"<iso-8601>"|null,"input_idle_s":<number>|null}`

Two steps, in this order: a **momentary** `SetThreadExecutionState(ES_DISPLAY_REQUIRED)` — never `ES_CONTINUOUS`, because a standing claim nobody clears is indistinguishable from a broken power plan — then a zero-delta pointer move (`SendInput`, `MOUSEEVENTF_MOVE`, `dx = dy = 0`). **Never a key event.** The synthetic-input structure the companion declares has a pointer member and no keyboard member, so there is nothing to fill in even by mistake, and a structural test fails if any keyboard API name appears in a display source file. Advertised unconditionally: waking a screen is the exact inverse of the one operation that takes something away.

### `desktop.display_status`

Payload: `{}`
Result: `{"observed_state":"unknown"|"off"|"on"|"dimmed","observed_at":"<iso-8601>"|null,"input_idle_s":<number>|null,"monitors":[{"index":<i>,"primary":<bool>,"left":<i>,"top":<i>,"width":<i>,"height":<i>}]?}`

- **Observed, never inferred.** The companion runs a message-only window registered for `GUID_CONSOLE_DISPLAY_STATE` (0 off, 1 on, 2 dimmed) and `GUID_MONITOR_POWER_ON` (0 off, 1 on); the state is the last value it was handed, with the moment it arrived. Before the first notification the answer is `unknown` with a null `observed_at`, and it stays `unknown` — a device that guessed "on" from a recent keystroke would be reporting the idle timer twice under two names. Windows offers no reliable synchronous "is the display on?" call, and the ones that look like it are the same APIs that turn a display off, so the observer holds no way of driving a display at all.
- **`monitors` is reporting only.** From `EnumDisplayMonitors`; **absent** (not empty) when the session could not enumerate them, because "could not tell" and "this machine has no monitors" are different claims. M18.3 changes no topology, resolution, orientation or primary-monitor choice, and a structural test fails if a display-configuration API name appears.
- **`input_idle_s` is `null` when unknown**, which is a different statement from `0`.

### `desktop.display_off` (changed in M18.3)

Payload: `{"reason":"<short token>"?,"holdoff_s":<int>?}` — `holdoff_s` defaults to **120**, clamped to 0…3600. A non-integer `holdoff_s` is a `validation_error`; an out-of-range one is clamped, because an absurd number still describes an intent while a non-number does not.

Result on a **refusal**, which is a **successful** `command_ack` and not an error:

```json
{"display_off":false,"refused":"recent_input"|"alarm_active",
 "input_idle_s":<number>|null,"holdoff_s":<int>,
 "observed_state":"…","observed_at":"…"|null}
```

Result on a real off:

```json
{"display_off":true,"method":"wm_syscommand_monitorpower",
 "input_idle_s":<number>|null,"holdoff_s":<int>,
 "observed_state":"…","observed_at":"…"|null,"monitors":[{…}]?}
```

- **Why a refusal is a success.** Nothing failed: the device looked, and the answer was no. An error class here would make a correct refusal indistinguishable from a broken device, and the caller would retry it.
- **`recent_input`** when the idle time is known and strictly less than `holdoff_s`. 120 s of quiet is enough; 119.9 s is not. An **unknown** idle never satisfies the holdoff by itself and never blocks the off either — the device does not fabricate a zero (refuse forever) or a large value (claim quiet it never observed).
- **`alarm_active`** whenever an alarm is ringing, checked first and whatever the idle timer says. Three hours of quiet is exactly the state a wake alarm fires into; darkening the screen at that moment is the machine working against the thing it just did.
- **The observed state on a successful off is read back after the broadcast**, so a broadcast nothing honoured cannot be reported as a dark screen. It may still be `unknown` on a device that was never told.
- The two gates of §6d are unchanged and are checked first: with `DisplayPowerEnabled=false` the answer is `capability_missing` (not a refusal result), from the service before the companion is consulted and from the companion again.

## 6f. Capability pair: `desktop.alarm_arm` / `desktop.alarm_disarm` (M18.3)

The **local fallback** for an alarm the cloud intends to ring. A wake alarm that only rings when the cloud can reach the device is not a wake alarm; it is a wake alarm plus an availability requirement the owner never agreed to. So when Cloud Core schedules a wake-up it also ARMS the device. Both names are advertised unconditionally; the companion persists arms at `%LOCALAPPDATA%\PagentOS\companion\armed-alarms.json` (owner profile, not the service's machine tree), written atomically by write-then-move.

`desktop.alarm_arm`

Payload: `{"alarm_id":"<id>","fire_at":"<iso-8601>","grace_s":<int>?,"label":"<text>"?,"wake_volume":{…}?,"max_duration_s":<int>?}`
Result: `{"armed":true,"alarm_id":"…","fire_at":"…","grace_s":<i>,"fire_local_at":"…","replaced":<bool>,"armed_count":<i>}`

`desktop.alarm_disarm`

Payload: `{"alarm_id":"<id>"?}` — omit it to forget every arm.
Result: `{"disarmed":true,"alarm_id":"…"|null,"was_armed":<bool>,"armed_count":<i>}`

- **`alarm_id` is required to arm.** Unlike `alarm_start`, an anonymous arm could never be consumed by anything except its own firing.
- **`grace_s` defaults to 60**, clamped to 0…3600. The device rings at `fire_at + grace_s`, not at `fire_at`: firing at exactly `fire_at` would race the cloud's own command over a link with any latency at all, and the owner would sometimes hear two alarms.
- **One ring per `alarm_id`, always.** Three things consume an arm, each removing it from the store first: a `desktop.alarm_start` naming that id (the ordinary case — the cloud got there in time), a `desktop.alarm_stop` naming it (the owner has already dealt with it), and `desktop.alarm_disarm`. Firing locally removes it too, **persisted before the first sample is generated**, so a companion that dies mid-ring gives the owner a missed alarm — which they notice — rather than a second one on the next start.
- **Arming is idempotent by `alarm_id`**: the same id replaces the existing arm and says so in `replaced`. Disarming is idempotent the way `alarm_stop` is: disarming something never armed is a success with `was_armed:false`.
- **Reload on start is conservative.** An arm whose local fire time already passed rings **once** if it passed less than **2 hours** ago, and is **expired with an audit row** otherwise. Waking someone twenty minutes late is a late alarm; waking them at 14:00 for a 06:30 alarm is a machine behaving badly.
- **The fallback rings the same alarm.** `label`, `wake_volume` and `max_duration_s` are carried into the `alarm_start` path of §6c, so the local ring obeys the same ramp, the same ceiling and the same `max_duration_s`. With no render endpoint the failure is audited once per arm, not once per tick.
- Audit rows: `alarm_arm`, `alarm_disarm`, `alarm_arm_consumed`, `alarm_arm_fired`, `alarm_arm_expired`, `alarm_arm_ring_failed`.

Configuration (companion): `PAGENTOS_AGENT_ArmedAlarmStorePath` overrides the default path.

## 6g. Capability: `desktop.activity_status`, and the heartbeat's `status` (M18.3)

Payload: `{}`
Result — and, verbatim, the optional `status` object on a heartbeat:

```json
{"input_idle_s":<number>|null,
 "display_state":"unknown"|"off"|"on"|"dimmed",
 "display_observed_at":"<iso-8601>"|null,
 "alarm_ringing":<bool>,"ringing_alarm_id":"…"|null,
 "armed_alarms":<int>,"next_alarm_at":"<iso-8601>"|null}
```

- **One shape, one producer.** The heartbeat's `status` IS this capability's result. A status assembled separately from the capability's answer would drift, and the version Cloud Core reasons about most often would be the one nothing tests.
- **`input_idle_s` comes from `GetLastInputInfo` — a tick count and nothing else.** No key, character, pointer position, window title or application name is ever read, stored or sent. A structural test fails if a hook, key-state, raw-input or foreground-window API name appears in the companion's input source.
- **It reports; it does not decide.** There is no "the owner is asleep" field and there will not be one. The inference from idle time and display state to a person's state belongs where it can be explained, argued with and turned off — in Cloud Core, against the presence model (`docs/M18_THREAT_MODEL.md` §4).
- **On the heartbeat the field is OPTIONAL and additive; `protocol_version` stays 1.** The Device Service asks the companion for `desktop.activity_status` before each heartbeat and attaches the answer. Absent means "not known", never "nothing is happening": no companion connected, a companion that did not answer within **1.5 s**, a companion that threw, or an agent older than the field all produce a heartbeat without `status`, byte-for-byte the frame v1 always sent. Presence and liveness never depend on it — a status path that could stall a heartbeat would let a busy companion make the device look offline, which is strictly worse.
- **The key set is closed and the SERVICE closes it.** `additionalProperties: false` inside `status` means one unknown key would fail the broker's validation for the whole heartbeat, so the Device Service projects the companion's answer onto exactly the seven keys above before sending. A newer companion cannot break an older service's connection.

## 6h. Capability: `desktop.play_audio` (M18.3)

Plays ONE short piece of audio the owner's own broker rendered — a spoken greeting after a wake alarm — in the owner's interactive session. Advertised unconditionally; **interactive-session capability, executed by the Session Companion**, under the desktop family's 60 s cap, and it **blocks until the audio has finished** so a caller sequencing "ring, then greet" gets an honest completion.

Payload: `{"audio_id":"<id>","audio":{"url":"<https://broker/…>","sha256":"<64 hex>","bytes":<int ≤ 2097152>,"format":"wav"},"level":<0..1>?,"max_seconds":<int ≤ 20>?}`
Result: `{"played":true,"audio_id":"…","duration_ms":<int>,"level":<f>}`

Four bounds, all checked on the device:

- **Origin.** The **Device Service** refuses the command with `security_scope_error` (never retryable) before it reaches the pipe unless the URL's scheme, host and port equal the broker REST origin this device is configured for. A device with no configured broker origin refuses every `play_audio`: "we could not tell where audio may come from" and "this audio may be played" must not be the same answer. The same host on a different port is a different server.
- **Size.** `bytes` must be 1…2 MiB, and the bound is enforced **while reading** (a `Content-Length` is a claim). A body that differs from the declared size is a `validation_error`.
- **Digest.** The SHA-256 of the bytes that arrived must equal `audio.sha256`. A mismatch is `security_scope_error`, **not retryable**: what is at that URL is not what Cloud Core described, and fetching it again would not change that.
- **Duration and level.** `max_seconds` defaults to 20 and is clamped to 1…20; audio longer than that is **trimmed rather than refused**, because the owner asked to be greeted and a slightly short greeting serves that better than silence plus an error. `level` defaults to **0.75**, clamped to 0…1, and **scales the samples** — nothing in this capability touches the Windows master or endpoint volume, and a structural test fails if any endpoint-volume API name appears in any companion source.

Other rules: the fetch has a **10 s** timeout (`timeout`, retryable); a non-2xx answer is `dependency_unavailable` (retryable); the container must be RIFF/WAVE **PCM16**, mono or stereo, 8 000…192 000 Hz — anything else is a `validation_error` rather than a best-effort guess. The render endpoint is opened at the **WAV's own rate and channel count** and the shared-mode render path converts to the endpoint's mix format; there is no resampler in the agent. With no render endpoint the answer is `dependency_unavailable` (retryable), as for the alarm.

## 6i. Capability family: the Digital Operator (M19)

The operator family rides this protocol unchanged — one command envelope, one lifecycle, one idempotency store, one audit log. The binding specification is **`docs/M19_DIGITAL_OPERATOR_SPEC.md`** (ADR-0082); this section states how the family is carried on the device side and what the companion guarantees.

**Names** (`AgentCapabilities.Operator`, 32, no family marker — every name is an operation):
`app.launch`, `app.list`, `app.activate`, `app.close`; `window.list`, `window.current`, `window.activate`, `window.minimize`, `window.maximize`, `window.restore`, `window.move`, `window.resize`, `window.close`; `keyboard.type`, `keyboard.key`, `keyboard.shortcut`; `pointer.move`, `pointer.click`, `pointer.double_click`, `pointer.right_click`, `pointer.scroll`; `ui.inspect`, `ui.invoke`, `ui.set_value`, `ui.select`; `screen.capture`, `screen.inspect`; `file.open`, `file.reveal`; `terminal.open`, `terminal.execute`, `terminal.status`.

- **Advertisement.** The family appears in `hello.capabilities` and the enrollment manifest **only when `OperatorEnabled=true`** on the service (the installer's `-Operator` switch; the `capabilities` verb reports `operator_enabled`) AND on the companion (`PAGENTOS_AGENT_OperatorEnabled=true`, which is what builds the operator object at all). Order in the manifest: desktop, alarm, ambient, display, browser, operator — an append, never a reshuffle.
- **Routing.** Every name is interactive (`AgentCapabilities.IsInteractive`): the Device Service (Session 0) forwards it over the authenticated companion pipe and never touches a window itself. With `OperatorEnabled=false` the service answers `capability_missing` (not retryable) before the pipe; a companion without the operator answers the same; no companion connected is `dependency_unavailable` (retryable), as for every family. There is no other path to the desktop: no REST route, no script, no worker.
- **Caps and budget.** The service caps one command at **30 s** for the family and **15 s for `app.launch`** (a 10 s window wait plus headroom). The pipe request carries `timeout_ms` and `deadline_utc_ms` as for the browser family; the companion budgets what is LEFT less the same 500 ms headroom, cancels the action when it elapses and answers `timeout` (retryable). A cancelled command answers `cancelled`. Operator requests run on the companion's concurrent (long-running) path so the heartbeat's status request is never behind a launch — but the operator itself executes **one action at a time** (one pair of hands).
- **Every result carries `observed: {…}`** — a fresh read AFTER acting: the re-read window (`window_id, pid, image, title, state, rect{x,y,width,height}, foreground, class_name, owned`), the value read back, the process still alive, the cursor, the selection. A window action whose re-observed state is not the requested one (`window.maximize` observed `normal`, `window.move` off by more than **8 px**) fails with **`postcondition_failed`** (retryable) instead of succeeding on the strength of the call having returned. `app.activate` / `window.activate` verify `GetForegroundWindow` after up to 3 attempts.
- **`window_id`** is `w-<hwnd>-<tick at first sight>`, issued by the companion's registry the first time it sees a window and stable until that handle is gone. An id the registry never issued — a made-up one, one from a previous companion life — is `ui_target_not_found` (retryable): observe again and re-resolve. Handles never cross the pipe on their own.
- **The focus guard (spec §1, invariant 2).** Immediately before any `keyboard.*` or `pointer.*` event the companion compares the payload's window — handle, pid, image and the first 24 characters of the title, all freshly read — with the actual foreground window. A mismatch is **`focus_mismatch`** (retryable) naming both windows; nothing is sent, and the companion never activates the target on its own — activation is `window.activate`, an observable action the planner asks for.
- **Payload validation, before anything touches a window.** Required fields and types; `text`/`value` ≤ **4096** chars with no control characters except tab and newline; **`secret: true` is refused with `validation_error` "secrets are never typed"** on `keyboard.type` and `ui.set_value`; `key` from the fixed list (`enter escape tab backspace delete insert home end pageup pagedown up down left right space f1..f12`); `keys` = 1–3 of `ctrl alt shift` plus exactly one key (a letter, a digit or a named key) — `win` is not offered; pointer `x,y` inside the window (`space: "window"`, the default) or inside the virtual screen (`space: "screen"`), `delta` in ±50 notches and non-zero; `depth` ≤ 5 and `max_nodes` ≤ 200 on `ui.inspect`; `timeout_s` ≤ 30 on `terminal.execute`. Every result passes the browser family's forbidden-key scan (`cookie authorization setcookie localstorage sessionstorage password token secret apikey`, normalised substring, any depth) before it leaves the companion → `security_scope_error`; a UI element that says it is a password field is described as `masked: true` and its value is never read.
- **`app.launch`** takes an allowlisted name (`notepad`, `calc`, `explorer`, `powershell`, `chrome`, `msedge`) or an absolute `.exe` under Program Files / Windows; anything else is `permission_denied`. It waits ≤ 10 s for a top-level window of the new pid and answers `{pid, window_id|null, title|null, executable, observed:{window_appeared, window, process_alive}}`. **`app.close`** posts WM_CLOSE to the process's windows and waits ≤ 5 s; a dialog that appears meanwhile (an owned window or a `#32770` of that pid) stops the wait early and is reported as `closed:false, modal:{window_id, window, dialog:{title, buttons:[…], texts:[…]}}` described through UI Automation, so the planner can `ui.invoke` the right button by name; a process still alive after the wait is reported, not killed, unless `force:true` (then `method:"terminated"`). `window.close` behaves the same for one window.
- **`ui.*`** run over `System.Windows.Automation` (the Windows Desktop runtime's UIAutomationClient, referenced directly; the companion stays a console process). Elements are `{automation_id, name, control_type, class_name, enabled, bounds{x,y,width,height}|null, value?, masked?, selected?, toggle_state?, children}`; an element is named by `automation_id`, `name`, `name_prefix` (optionally with `control_type`), and `ui.inspect` may start at such an element. `ui.invoke` uses Invoke, else Toggle, else SelectionItem, else ExpandCollapse. `ui.set_value` uses the Value pattern, else — for a native text window that exposes only the Text pattern (classic Notepad's multi-line edit is such a Document) — `WM_SETTEXT` to that window; either way the value is **read back** and reported as `observed_value`. `ui.select` selects the named child item and reports the container's selection read back.
- **`screen.capture`** is `PrintWindow` of one window or `BitBlt` of the primary screen, PNG-encoded by a small managed encoder (no `System.Drawing`), `{width, height, png_base64, bytes, scale}`; over **2 MiB** it is halved (scale 2, then 4) and refused if still over; nothing is written to disk. **`screen.inspect`** is `{monitors:[…], virtual_screen, foreground, cursor}`.
- **`file.open` / `file.reveal`** accept only an absolute path inside the owner's authorised roots (`OperatorRoots`, default the profile directory) — outside is `permission_denied`, an executable extension on `file.open` likewise. `file.open` with `application` launches the allowlisted app on the path; without it, ShellExecute. `file.reveal` runs `explorer.exe /select,<path>`, waits for the new Explorer window and reports the list's selection read back (`observed.selection`).
- **The terminal is an allowlist** (`TerminalAllowlist`, default `hostname`, `whoami`, `ipconfig`, `Get-Date`, `Get-ComputerInfo -Property *`, `Get-Process -Name *`, `Get-ChildItem <path>`; `*` = one token of anything, `<path>` = one absolute path under an authorised root, token counts must agree). A command carrying `; | & $ ( ) { } < > \`` or a newline, or not matching a pattern token for token, is **`permission_denied` before a process exists**. What runs, runs headless — `powershell.exe -NoProfile -NonInteractive -Command`, no window, output UTF-8, System32 first on the child's PATH so an allowlisted name resolves to the system binary — with a hard timeout (`timeout_s`, default and maximum 30 s; the process tree is ended on timeout and on cancel), and answers `{exit_code, stdout, stderr, duration_ms, truncated, matched}` (output bounded at 32 KiB). `terminal.open` opens a visible PowerShell window (`{pid, window_id}`); `terminal.status {pid}` answers `{alive, exit_code?}`.
- **Audit and log.** One `operator_request` row per request — capability, outcome class, `duration_ms`, never payload text, never a window title — plus the companion log line; the service writes its usual per-command rows.

Configuration (all `PAGENTOS_AGENT_`-prefixed): service `OperatorEnabled` (default false); companion `OperatorEnabled` (default false), `TerminalAllowlist` (`;`-separated patterns; empty = the default list), `OperatorRoots` (`;`-separated directories; empty = the owner's profile). Error classes added for the family, in the schema and the broker's mirror: `focus_mismatch` (retryable), `permission_denied` (never), `postcondition_failed` (retryable).

## 7. Audit

Broker persists an `audit_events` row for: enrollment, session start/end, command created, delivered, each ack transition, cancel, expiry. Events carry `trace_id`, `device_id`, `command_id`, never secrets or payload bodies larger than 4 KB.
The agent writes a local append-only JSONL audit log (`%ProgramData%`/configurable) for every executed command.

## 8. Broker REST surface (owner API)

Since M9 every endpoint below requires `Authorization: Bearer <owner-session-token>` (ADR-0027) except `POST /enroll`, which is authenticated by the owner-minted enrollment token.

- `POST /v1/devices/enrollment-tokens` → one-time token (owner session + loopback peer)
- `POST /v1/devices/enroll` (enrollment token only — see §2)
- `GET /v1/devices` — includes `status: online|offline|revoked`, `last_seen_at`
- `POST /v1/devices/{device_id}/commands` `{capability, payload, idempotency_key?, timeout_s?}` → `202 {command_id,status}` (immediately durable; never long-polls execution)
- `GET /v1/devices/{device_id}/commands/{command_id}` → full status/result/error
- `POST /v1/devices/{device_id}/commands/{command_id}/cancel`
- `POST /v1/devices/{device_id}/revoke`

## 9. Windows agent process architecture

- **Device Service** (background; Windows Service-capable, Session 0): owns the keypair, the WS connection, idempotency store, local audit log, and machine-level capabilities. Never touches the interactive desktop.
- **Session Companion** (runs in the owner's interactive session): executes every interactive capability — `desktop.open_application`, `desktop.open_artifact`, the `browser.*` family, since M18 `desktop.alarm_start` / `desktop.alarm_stop` (audio in the owner's session) and `desktop.display_off` (a broadcast that only reaches the owner's windows), and since M18.3 `desktop.display_wake` / `desktop.display_status` (a message-only window registered for the console display-state notifications, plus a momentary display-required request and a zero-delta pointer move), `desktop.activity_status` (a `GetLastInputInfo` tick count, which the Device Service also attaches to each heartbeat), `desktop.alarm_arm` / `desktop.alarm_disarm` (a local fallback ring persisted in the owner's profile) and `desktop.play_audio` (one bounded, digest-verified greeting on the same render path as the alarm), and since M19 the Digital Operator family (§6i: windows, keyboard and pointer under the focus guard, UI Automation, screen capture, files inside the authorised roots and the allowlisted headless terminal — behind `OperatorEnabled`) — and connects to the Device Service over an authenticated local named pipe. Every one of those needs the owner's session: a Session-0 process has no display to observe, no input to time, no render endpoint and no window a broadcast can reach. If no companion is connected, interactive commands fail fast with `dependency_unavailable` (`retryable: true`), and the heartbeat simply carries no `status`.
- In development both run as console processes; installing the service (requires UAC) is an owner action recorded in `OWNER_ACTIONS_MINIMAL.md`.
