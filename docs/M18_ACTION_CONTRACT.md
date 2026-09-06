# M18 Action Grounding + Live State Contract

Status: binding for the M18 fix (2026-09-06, owner defect report). Two owner-observed defects:

1. `Gözünü kapat.` really disabled the eye, and the assistant said *"öyle olmuş gibi düşün"*:
   the spoken acknowledgement was not grounded in the real terminal result.
   `Gözünü aç` / `Kamerayı aç` / `Beni tekrar izle` did nothing: no enable path existed.
2. `Kendi sisteminde şu anda ne görüyorsun?` was answered with bookkeeping narration
   ("kayıtlara bakmalıyım…", counts of truth kinds) instead of the current state.

This document is the contract both halves (Cloud Core `services/api`, web client `apps/web`)
implement. Anything not written here is the implementer's call; anything written here is not.

## 1. The rule

```
OWNER COMMAND -> normalize intent -> authorize -> execute real capability
             -> wait for terminal ACK -> read back resulting runtime state
             -> only then narrate success
```

WRITE → READ-BACK → SPEAK. Never UNDERSTAND → ASSUME → SPEAK. No spoken claim that a
system/physical mutation happened unless the receipt's `terminal_status` is `verified`
(or `already`). Banned in any assistant speech about a mutation:
`yapmış gibi düşün`, `olmuş gibi düşün`, `gibi düşün`, `sayabiliriz`, `varsayalım`,
`oldu varsay`. These strings live in one place (`app/actions/receipt.py:FAKE_COMPLETION_PHRASES`)
and a test asserts no speech template contains one; the persona forbids them by name.

## 2. One router, three classes

`app/voice/intents.py::resolve_intent` is the ONLY Turkish command interpreter. It gains:

- `klass: "query" | "action" | "control"` on `ResolvedIntent` (and in `to_dict()`),
- `capability: str | None` — the canonical capability an ACTION targets.

`app/explain/classify.py` stays the QUERY-kind table and is reached only through
`resolve_intent` (already the case: `query_kind`). No third table anywhere. The web client
has none and must not grow one (its only Turkish strings are labels).

| Utterance | klass | intent / query_kind | capability / tool |
|---|---|---|---|
| `Gözünü kapat.` `Kamerayı kapat.` `Beni izleme.` | action | `EYE_DISABLE` | `eye.disable` |
| `Gözünü aç.` `Kamerayı aç.` `Beni izle.` `Beni tekrar izle.` `Gözünü tekrar aç.` `Active Eye'ı aç.` | action | `EYE_ENABLE` (new) | `eye.enable` |
| `Kamera açık mı?` `Göz açık mı?` | query | `eye_state` (new query kind) | `state.now` |
| `Kendi sisteminde şu anda ne görüyorsun?` `Sistemin şu anda ne durumda?` | query | `world_state` | `state.now` |
| `Son yaptıklarını anlat.` | query | `last_activity` | `activity.explain` (ledger) |
| `Ne öğrendin?` | query | `learned` | `activity.explain` (experience) |
| `Bunu canlıya alabilir misin?` | query | `can_deploy` | `activity.explain` (policy) |
| `Canlıya al.` | action | `DEPLOY` (new) | `release.promote` → **refused** receipt |
| `Dur.` | control | `STOP` | — |

Imperatives with a matching capability are ACTIONS. "Ses bağlı mı", "cihaz çevrimiçi mi",
"şu an ne çalışıyor" are `world_state` queries.

Authority: `release.promote` by voice is always `execution_status: refused`,
`error_class: owner_authorization_required`, speech
`"Canlıya alma kararı sizin efendim; onayı Core'daki Onay Merkezi'nden verirsiniz. Ben kendi başıma canlıya almam."`
It is still a recorded action (ledger `action.receipt`) so the refusal is evidence.

## 3. Authoritative sources by question class

| Class | Source | Examples |
|---|---|---|
| CURRENT STATE | live runtime → World Model snapshot → recent verified state (`state.now`) | camera open? voice connected? presence now? device online? what is running? |
| HISTORICAL | Activity Ledger (`activity.explain`) | son ne yaptın, dün ne oldu |
| LEARNED | Memory / Experience (`activity.explain` kind `learned`) | ne öğrendin |
| CODE / SELF | Self Model (`activity.explain` kind `self_code`) | kendi kodunda ne var |
| GOALS | Goal Engine (`activity.explain` kind `goals`) | hedeflerin ne |
| AUTHORITY | policy (`activity.explain` kind `can_deploy`) | canlıya alabilir misin |

`activity.explain` with resolved kind `world_state` / `eye_state` MUST delegate to the same
live composer `state.now` uses (`app/state/now.py::compose_live_state`), so the answer is
identical whichever tool the model picked. The ledger is never the source for "now".

## 4. `state.now` (QUERY tool)

Registry name `state.now`, not long-running, no preamble. Arguments:
`{"question": str (≤500), "scope": "all" | "eye" | "voice" | "presence" | "devices" | "release" (optional)}`.

Result:

```json
{
  "query_kind": "world_state" | "eye_state",
  "subsystem": "worldmodel",
  "observed_at": "<iso>",
  "facts": [
    {"key": "cloud_core.health", "value": "ok", "source": "health_probe",
     "observed_at": "<iso>", "age_s": 0.4, "confidence": 1.0, "stale": false}
  ],
  "uncertainties": [{"subject": "owner.presence", "reason": "no_observations_yet"}],
  "speech": "Cloud Core sağlıklı. Ses bağlı. Göz açık; şu an geçerli bir varlık gözlemim yok. Bir modül canlıya alınmayı bekliyor."
}
```

Facts to compose (each with source / observed_at / age_s / confidence / stale, missing ones
become uncertainties, never guesses): `cloud_core.health` (system health), `voice.session`
(THIS realtime session: connected), `eye.enabled` (durable flag) + `eye.last_observation_age_s`
(most recent accepted camera observation, if any), `owner.presence` (engine assertion:
state, confidence, age, stale), `devices.online` (count / names), `release.shadow_ready`
(count awaiting owner), `tasks.running` (count). `stale` = past its source's TTL.

Speech rules: result first; 1–3 sentences for `scope=all`, 1 sentence for a single scope;
Turkish; no IDs, no counts of truth kinds, no "kayıtlara bakıyorum". Stale facts are said as
stale: `"Son doğrulanmış varlık gözlemi 48 saniye önceydi; şu an kesin doğrulayamıyorum."`
Eye scope: `"Göz açık efendim; son gözlem 5 saniye önce."` / `"Göz kapalı efendim."` /
`"Göz açık görünüyor ama 70 saniyedir gözlem gelmiyor; kamerayı doğrulayamıyorum."`

`session_activity` reports `query_kind` and `subsystem` for `state.now` calls exactly like
`activity.explain` calls (the harness correlates on them). Ledger row: `voice.state_answered`
(subsystem `voice`, detail `{query_kind, scope, facts, uncertainties, stale}`), never the text.

## 5. Eye actions (`eye.enable`, `eye.disable`)

### 5.1 Who does what

The camera lives in the browser; the durable flag lives in Cloud Core. One command, one
round trip, synchronous from the model's point of view (NOT long-running, no preamble):

```
provider function_call eye.disable {utterance}
  -> client: execute LOCAL capability first (EyeStore.disable -> perception loop stops,
             camera track released), bounded 8 s
  -> client: relay POST /tool-calls with arguments = {utterance, observed_after: {...}}
  -> server handler: disable_eye(db, reason="voice:<matched>") [idempotent],
             read back is_eye_enabled(db), build ActionReceipt, ledger action.receipt,
             return succeeded {receipt..., speech}
  -> client: submit the result to the provider; the model reads `speech` verbatim
```

`eye.enable` mirrors it: local `EyeStore.enable` (getUserMedia may prompt) FIRST; the durable
flag is set by the server handler ONLY when the client's `observed_after.local.state` is
`ACTIVE` ("never tell the Cloud Core perception is on before the camera actually opened").

`observed_after` (client → server, inside `arguments`):

```json
{"local": {"state": "ACTIVE|DISABLED|ERROR", "running": true, "camera_label": "…" | null,
           "error_class": "<client error class>" | null,
           "observed_at": "<iso>", "changed": true,
           "media_track_ready_state": "live" | "ended" | null,
           "action_trace": ["getUserMedia", "track_live", "loop_started"]}}
```

Client error classes (`app/actions/receipt.py:CLIENT_ERROR_CLASSES`, each with its own
sentence in §5.2): `permission_denied`, `device_not_found`, `device_busy`,
`device_unavailable` (legacy: not-found-or-busy), `get_user_media_failed`,
`stream_created_but_track_ended`, `perception_start_failed`, `state_transition_failed`,
`timeout`, `capability_missing`.

`media_track_ready_state` is the camera track's `readyState` at `observed_at` and
`action_trace` is the client's own step list (≤ 12 short strings; the server clips to 12 ×
80 chars). Both are optional evidence: the server never fails on their absence. It echoes
`media_track_ready_state` in the receipt's `observed_after.local` and stores
`action_trace` on the receipt. An enable relayed as `ACTIVE` with the track `ended` is not
an open camera: the server treats it as `failed` / `stream_created_but_track_ended` and
does not set the durable flag.

If the client has no eye capability at all (a non-web client), it relays without
`observed_after`; the server treats that as `local.state = "ERROR", error_class =
capability_missing` and never sets the durable flag on enable. (A disable is still written
durably in that case - privacy is the server's side too - and its `execution_status` says
so: `executed` when the flag changed.)

### 5.2 Server handler

- `requested_state`: `disabled` / `active`.
- Before: `was_enabled = is_eye_enabled(db)`.
- disable: `disable_eye(db, reason)` (idempotent, see 5.4); enable: `enable_eye(db, reason)`
  only if `local.state == "ACTIVE"`.
- Read back `now_enabled = is_eye_enabled(db)` (a read-back that raises is `None`).
- `physical_ok` (the browser's own account): disable → `local.state == "DISABLED"`;
  enable → `local.state == "ACTIVE"` and `media_track_ready_state != "ended"`.
- `terminal_status`:
  - `verified` — `physical_ok` AND `now_enabled == (requested == active)` AND the state
    changed in this command (the handler's write returned `True`, or the client relayed
    `local.changed = true` because its own durable call landed first);
  - `already` — `physical_ok`, server matches, nothing changed (the eye was already so
    before this turn);
  - `failed` — `local.state == "ERROR"`, or an enable with the track `ended` (speech by
    `error_class`);
  - `unverified` — anything else: the write raised (`error_class: durable_write_failed`),
    the flag could not be re-read (`read_back_failed`), or server and local disagree
    (`state_mismatch`).
- `execution_status`: `executed` (a write happened), `noop` (already), `failed` (the write
  raised or the capability could not do it), `refused`.
- Speech (exact strings, `app/actions/receipt.py`). Truthful in both directions: below
  `verified` the sentence never says "kapattım/açtım", and when the browser reports the
  camera physically in the requested state it never says "kapatamadım/açamadım" either:
  - disable verified: `Gözümü kapattım efendim.`
  - disable already: `Gözüm zaten kapalı efendim.`
  - disable unverified, camera closed by the browser's account (write raised / read-back
    mismatch): `Kamera kapandı ancak işlem kaydını doğrulayamadım.`
  - disable unverified, browser reports the camera still running; disable failed
    (`capability_missing`): `Kamerayı kapatamadım; işlem doğrulanmadı.`
  - enable verified: `Gözümü açtım efendim.`
  - enable already: `Gözüm zaten açık efendim.`
  - enable unverified, camera open with a live track by the browser's account:
    `Kamera açıldı ancak işlem kaydını doğrulayamadım.`
  - enable failed `permission_denied`: `Kamerayı açamadım; tarayıcı kamera izni vermedi.`
  - enable failed `device_not_found`: `Kamerayı açamadım; kamera bulunamadı.`
  - enable failed `device_busy`: `Kamerayı açamadım; kamera başka bir uygulama tarafından kullanılıyor.`
  - enable failed `device_unavailable` (legacy): `Kamerayı açamadım; kamera bulunamadı ya da meşgul.`
  - enable failed `get_user_media_failed`: `Kamerayı açamadım; tarayıcı kamera akışını başlatamadı.`
  - enable failed `stream_created_but_track_ended`: `Kamera açıldı ama görüntü akışı hemen kesildi.`
  - enable failed `perception_start_failed`: `Kamera açıldı ama algılama döngüsü başlatılamadı.`
  - enable failed `state_transition_failed`: `Kamerayı açamadım; durum geçişi tamamlanamadı.`
  - enable failed `timeout` / `capability_missing` / unknown class; enable unverified with
    the camera not open: `Kamerayı açamadım; işlem doğrulanmadı.`

### 5.3 One canonical mutation path: the tool. No server-side safety net.

There is no server-side safety net any more, for disable or enable. `record_client_events`
resolves an `utterance` to `EYE_DISABLE` / `EYE_ENABLE` and audits it (intent, `klass`,
`capability`, `query_kind`) — and does nothing else. It never calls `disable_eye`, keeps no
`eye_safety` bookkeeping, and the eye tool handler has no same-turn window.

Why: the original net ("privacy must not depend on the model") was a second, hidden
mutation path with no receipt. In the owner's session `3eb6fee7` (2026-09-06 13:57Z) the
client relayed every eye tool call without `observed_after`, so every receipt was
`capability_missing` / `failed` — and the camera still closed at 13:57:33, written by the
net 7 ms after the tool call with reason `voice:gözünü kapat`. The owner's record showed
an action that failed and a camera that closed, and nothing that connected the two. The
owner's rule is exactly ONE mutation path per capability, the one that ends in a receipt.
Privacy is served by the tool the persona makes the model call always (§6), by the
`eye.disable` handler writing the durable flag regardless of what the client reported,
and by the owner's own button; a model that fails to call the tool is a persona defect to
fix where it is, not a reason for a write nobody narrates. A regression test pins that an
`EYE_DISABLE` utterance leaves the eye exactly as it was.

### 5.4 Idempotent durable writes and presence invalidation

`app/presence/eye.py::_set_eye_state` becomes idempotent: when `is_eye_enabled(session)`
already equals the requested value it writes no ledger row, publishes nothing and returns
`False`; otherwise it writes, publishes and returns `True`. `enable_eye` / `disable_eye`
return that bool.

On a real disable (returned `True`), the presence service invalidates camera evidence:
`presence_service.on_eye_disabled(now)` drops camera-sourced observations from the fusion
window, sets the current assertion to UNKNOWN with reason `eye_disabled`, publishes the
degraded state once, and resets the heartbeat. The World Model then shows
`device.camera_state=disabled` and an `owner.presence` uncertainty `eye_disabled`. Opening
the camera never asserts presence (already tested).

### 5.5 Receipt (common to every future mutating capability)

`app/actions/receipt.py`:

```python
@dataclass(frozen=True)
class ActionReceipt:
    action_id: str            # the tool call_id, or a uuid for non-voice actors
    capability: str           # "eye.disable"
    requested_state: str      # "disabled"
    execution_status: str     # executed | noop | refused | failed
    terminal_status: str      # verified | already | unverified | failed
    observed_after: dict      # {"server": {"eye_enabled": False}, "local": {...}}
    evidence_refs: list[dict] # [{"kind": "ledger_event", "ref": ...}, {"kind": "realtime_session", "ref": ...}]
    error_class: str | None
    speech: str
    started_at: datetime
    completed_at: datetime
    session_id: str | None     # the realtime session the command came through
    observed_at: datetime | None  # when the read-back was taken
    action_trace: list[str]    # the client's own steps, ≤ 12 × 80 chars
    def as_dict(self) -> dict: ...
```

`observed_after.server` for the eye is `{"eye_enabled": bool | null, "was_enabled": bool |
null, "changed": bool, "write_error": "<ExceptionName>" | null}`; `observed_after.local` is
the normalised client report of §5.1 (`state`, `running`, `camera_label`, `error_class`,
`observed_at`, `changed`, `media_track_ready_state`).

Ledger event `action.receipt` (subsystem of the capability, e.g. `presence`; `action` =
capability; `status` = terminal_status; `detail_json` = receipt without `speech`, so
`detail_json.session_id`, `detail_json.observed_at` and `detail_json.action_trace` are
queryable and a ledger query can correlate receipts by session). The tool result IS
`receipt.as_dict()` (with `speech`). `session_activity` exposes, per tool call,
`session_id` (the row's session), `capability`, `terminal_status`, `execution_status`,
`error_class`, `observed_at`, the receipt's `observed_after` (server + local, scalars only)
and `action_trace`, so the harness can print "browser eye state / media track / receipt"
per call from durable rows alone.

`GET /v1/state/now?scope=all|eye|voice|presence|devices|release[&session_id=<realtime>]`
(`app/state/routes.py`, owner-gated like `/v1/world`) returns exactly what the `state.now`
tool returns — the same `compose_live_state` over the same live runtimes — so a harness can
compare the runtime's view with the browser's without a voice session in the loop. It
writes nothing (no `voice.state_answered` row: nothing was told to the owner by voice).

## 6. Persona (services/api/app/voice/realtime_sessions/persona.py)

Add a block, in Turkish, that says: current-state questions → `state.now`; historical /
learned / goals / self / authority → `activity.explain`; `Gözünü aç/kapat`, `Kamerayı
aç/kapat`, `Beni izle/izleme` → `eye.enable` / `eye.disable` ALWAYS (never answer them
conversationally); `Canlıya al` → `release.promote` (it will refuse; read its speech). For
these tools: no preamble, no "bakıyorum", no narration of where the information comes from;
read `speech` verbatim; never say a mutation happened unless the tool said so; the banned
phrases by name. Default spoken answer: answer first, 1–3 sentences, no IDs, no filler;
`Kanıtı ne?` / `Teknik anlat.` open the detail.

## 7. Web client (apps/web)

### 7.1 `EyeStore` — `apps/web/app/lib/eye/store.ts`

The tab's ONE perception owner (mirrors `lib/voice/store.ts`): `getEyeStore()` singleton,
`installEyeStore()` for tests, `useSyncExternalStore` contract. State machine:

```
DISABLED -enable()-> ENABLING -camera open + POST eye/enable ok-> ACTIVE
ACTIVE   -disable()-> DISABLING -POST eye/disable + loop stopped-> DISABLED
any failure -> ERROR (error_class kept), then DISABLED on the next disable()
```

- `enable(reason)` / `disable(reason)` return a `LocalEyeResult = {state, running,
  camera_label, error_class, observed_at, changed}`; `changed=false` when already in the
  requested state (idempotent, no second stream, no fake transition).
- A second `enable()` while ENABLING joins the in-flight promise (one `getUserMedia`).
- `stopLocalOnly()` for the bus-driven stop (`eye.disabled` on the bus → local loop stops).
- `eyeInstances` counters: `sessions`, `cameraOpens`, `loopsStarted` — asserted `{1,1,1}`
  across two consumers + a remount + a double `enable()`, and `enable → ACTIVE → disable →
  DISABLED → enable → ACTIVE` with exactly two camera opens and never two loops at once.
- `useActivePerception` becomes a thin binding on the store; `EyeControl` behaviour unchanged.

### 7.2 The voice rig executes local eye actions

`ports.ts`: `LocalActionPort = { run(name: string, args: Record<string, unknown>): Promise<Record<string, unknown> | null> }`.
`browserRigParts` wires it to `getEyeStore()` for `eye.enable` / `eye.disable` (reason
`voice:<utterance>`), 8 s bound → `error_class: "timeout"`. The controller, in
`doRelayToolCall`, when `deps.localActions` answers non-null for the call, merges
`{observed_after: <answer>}` into the relayed `arguments`. Everything else in the relay path
is unchanged. The fake rig in tests carries a scripted `LocalActionPort`.

### 7.3 Tests (web)

`tests/eye/store.test.ts`: the state machine, idempotency, join-in-flight, counters, the
enable→disable→enable cycle through a fake frame source. `tests/voice/controller-local-actions.test.ts`
(or inside the store tests): a provider function call `eye.disable` runs the local action
BEFORE the relay and the relayed arguments carry `observed_after.local.state === "DISABLED"`;
a failing local enable relays `local.state === "ERROR"` with its `error_class`; the result's
`speech` reaches `submitToolResult` unchanged.

## 8. Tests (api)

- `test_voice_intents.py`: every utterance in §2 → klass / intent / query_kind / capability.
- `test_actions_receipt.py`: speech table (every client error class named; the
  physically-done-but-unverified sentences), banned phrases absent, `as_dict` shape with
  `session_id` / `observed_at` / `action_trace`, the ledger row carries them.
- `test_voice_eye_tools.py`: an `EYE_DISABLE` utterance never mutates (audited as
  action / `eye.disable`, eye unchanged, no ledger row, no `eye_safety`); disable verified
  (incl. track `ended`, client-did-the-write-first) / already / unverified (camera still
  running) / unverified-truthful (camera closed, write raised) / `capability_missing`
  (`executed`); enable verified (track `live`) / track `ended` → failed and flag NOT set /
  write raised → "açıldı ancak kaydını doğrulayamadım" / every named error class / no
  `observed_after` / already; `media_track_ready_state` and `action_trace` optional and
  bounded; `action.receipt` row with `session_id`; `session_activity` exposes
  `session_id`, `observed_after`, `action_trace`.
- `test_state_routes.py`: `/v1/state/now` owner-gated, the tool's shape, eye scope follows
  the durable flag, identical to `compose_live_state` for every scope, writes nothing.
- `test_voice_state_tool.py`: composer facts carry source/observed_at/age/confidence/stale;
  stale spoken as stale; eye scope sentences; `activity.explain` with a `world_state`
  question returns the same `speech` as `state.now`; concise (≤ 3 sentences, no banned words,
  no "kayıt").
- `test_presence_eye_invalidation.py`: disable → assertion UNKNOWN reason `eye_disabled`,
  camera observations dropped, World Model uncertainty; enable → no presence asserted.
- persona: contains the tool names and the banned phrases; no test reads the model.

## 9. Not in scope here

The M18 owner harness and the short Eye/Voice qualification (`scripts/core/`) are written by
the integrator against this contract. Display-off, mail, files, media, deployments adopt
§5.5 when they are built; nothing is retrofitted now.
