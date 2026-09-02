---
name: m12-voice-arbor-noise-security-review
description: Security review of commit a969375 (server voice/persona, ADR-0043) and merge f34cfd6 (web mic pipeline, ADR-0044) at 2026-09-02
metadata:
  type: project
---

Review scope: services/api voice `voice` field + persona style block (a969375); apps/web
mic read-back/calibration/gate/uplink/profile/diagnostics/events (f34cfd6).

**No Critical/High findings.** One Low (design fragility, not currently exploitable),
one Informational.

1. Low — `services/api/app/voice/realtime_sessions/routes.py` `create_session`
   (~lines 236-241): voice validation uses `getattr(provider, "require_supported_voice",
   None)` and silently skips validation if the selected `RealtimeProvider` doesn't
   implement that method. Only `OpenAIRealtimeProvider` implements it today;
   `SimulatedRealtimeProvider`/`FakeRealtimeProvider` don't, and this is confirmed
   intentional in `test_voice_realtime_sessions.py` ("the simulator has no
   supported-voice list -> any well-formed id is recorded as is"). Not exploitable now:
   the simulator is gated out of production by `simulator_allowed()` (ADR-0038,
   `runtime.py`) unless an operator explicitly sets
   `PAGENTOS_VOICE_REALTIME_SIMULATOR_ENABLED=true`, and even then the simulator makes
   no real vendor call. Client `voice` is still regex-bounded (`^[a-z]{2,16}$`, routes.py
   ~123) so no injection chars reach `cfg.voice` regardless. Fix: make the check
   fail-closed (refuse any client-supplied `voice` when the provider doesn't declare
   `require_supported_voice`/`supported_voices`) instead of duck-typed skip, so a future
   second real RealtimeProvider can't accidentally forward an unvalidated string into its
   vendor payload (`providers_openai_realtime.py:436` shows exactly this sink:
   `session["audio"]["output"]["voice"] = cfg.voice or self._voice`).
2. Informational — `apps/web/public/mic-check.html` is a static Next.js public asset,
   unauthenticated (no middleware.ts in apps/web), reachable outside `OwnerGate`/owner
   session. Harmless: it only reads local `getUserMedia`/`enumerateDevices` info and
   renders it into its own DOM via `textContent`, no network call, no exfiltration path.

**Confirmed clean / sound:**
- `voice_profile` (drives the Arbor persona style block) is set ONLY from
  `runtime.settings.voice_realtime_owner_target_voice_profile` (server config); it is
  not a field on `CreateSessionRequest`, and `ConfigDict(extra="forbid")` rejects any
  attempt by a client to smuggle one in. The style block itself
  (`VOICE_STYLE_ARBOR_TR`, persona.py) is a fixed constant selected by dict lookup, never
  string-built from request data — no template/prompt-injection path into the persona.
  `cfg.instructions` (the full persona) is always server-composed; a client cannot
  substitute its own (explicit comment + behavior confirmed in
  `providers_openai_realtime.py` `build_session_config`).
- Client `voice` field: regex `^[a-z]{2,16}$` at the Pydantic layer blocks any special
  characters; `OpenAIRealtimeProvider.require_supported_voice` (the real production path)
  checks it against a hardcoded `SUPPORTED_VOICES` tuple and raises `VALIDATION_ERROR`
  (422) otherwise — verified by `test_create_refuses_a_voice_the_provider_does_not_offer`.
  `speed` is clamped to `[0.25, 1.5]` at the provider constructor (raises `VoiceError`
  outside that range).
- Mic pipeline reporting (ADR-0044 §7) is genuinely numbers-only end to end: client
  `numbersOnly()` (`apps/web/app/lib/voice/events.ts`) drops every non-finite-number,
  non-boolean value and every forbidden key before an event is even built;
  `SpeechDetectorCalibration`/mic-metrics fields are typed as numbers at the source
  (`ports.ts` — `env`/`sensitivity`/`trigger`/`contaminated` are small integer codes, not
  strings), so there is no string/env-class field that could carry anything besides a
  number. Server-side `FORBIDDEN_KEY_PARTS`/`is_forbidden_key`
  (`realtime_sessions/service.py`) and client-side `FORBIDDEN_PAYLOAD_KEY_PARTS`/
  `isForbiddenKey` (`contract.ts`) are byte-for-byte identical (both lists and the
  normalize-before-match logic), and `MAX_EVENT_PAYLOAD_BYTES = 4*1024` matches on both
  sides (`routes.py` `MAX_EVENT_PAYLOAD_BYTES`, `contract.ts`). The separate `text` field
  on `ClientEvent` (max 4000 chars, used by the pre-existing "utterance"/"summary" event
  kinds, not touched by this diff) is a deliberately separate, already-bounded channel for
  transcript summaries — the new mic-calibration/mic-metrics call sites
  (`controller.ts` `onCalibration`/`reportMicMetrics`) never set `text`, confirmed by
  reading every call site.
- `MicrophoneProfile` (profile.ts) never leaves the browser: no `fetch`/`POST` anywhere
  in profile.ts; purely `localStorage` behind a wrapped store (throws degrade to
  in-memory). `fingerprint = FNV-1a(label|groupId)`: `groupId` is a W3C
  `MediaDeviceInfo` value that browsers salt per-origin, so it is stable across
  reloads/reattaches of the SAME origin (why the profile survives `deviceId` rotation)
  but not a cross-origin-correlatable identifier; `label` is a generic hardware/model
  string (no serial), shown only after mic permission is already granted. Diagnostics
  "copy JSON" (`voice/page.tsx` `copyReadBack`) is a local clipboard action the owner
  triggers deliberately (matches the documented workflow of pasting real device caps into
  `VOICE_OWNER_FEEDBACK.md`) — no automatic transmission.
- `normalizeProfile` (profile.ts) rebuilds the stored/foreign object field-by-field with
  strict type/enum checks (`pick()` against fixed `Set`s) rather than spreading raw
  localStorage content — no prototype-pollution path even if localStorage were tampered
  with via some other XSS.
- Uplink/gain never mutes: `WebAudioUplinkShaper.setGain` (audio.ts) clamps to
  `Math.max(0.1, ...)` (floor ~-20dB, never 0); the default `inputGainStrategy` is
  `"browser"` (`PassthroughUplinkShaper`, gain ignored entirely); no code path anywhere
  in audio.ts/controller.ts/gate.ts sets `track.enabled = false` or otherwise disables the
  microphone track. The gate's `playbackActive`/`echoExtraMarginDb` only raises the local
  *detection* threshold during assistant playback (barge-in still requires more evidence),
  it never touches the raw uplink track, which the ADR explicitly documents flows
  unmodified to the provider regardless of local gate state.
- No new leg/liveness bypass: `a969375`/`f34cfd6` don't touch leg-mismatch or attach
  logic; `voice`/`voice_profile` are purely additive fields threaded through existing
  payloads. `is_acceptance_evidence` (`realtime_bench.py`) is determined solely by
  `source == "client"` and this pre-existing trust boundary (owner-session-authenticated
  `/events`) is unchanged by this diff; the new `false_barge_ins`/`false_starts`/
  `false_turns` mic-metrics counters are NOT wired into `benchmark_report`'s
  `false_barge_count` (still hardcoded default 0 for client reports in
  `service.py::benchmark_report`) — so this diff adds no new way to inflate the
  official 5-metric acceptance benchmark; the gap is a missed-feature, not a new
  vulnerability.

Related: [[m4-voice-narration-security-review]] (same voice module family; that review's
open High finding about `device_trusted` self-assertion in `/voice/speaker/verify` is
unrelated to this realtime-session surface and still needs separate follow-up).
