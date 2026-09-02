# M12 — Realtime Voice Foundation: specification

Authority: `PROJECT_CONSTITUTION.md`, `docs/VOICE_SPEC.md`, ADR-0034, the M12 acceptance
standard in `docs/ACCEPTANCE_TESTS.md`, and `docs/VOICE_GAP_ANALYSIS.md` for what is kept.
The target is ChatGPT-Voice-class interaction as far as public APIs and this architecture
allow — perceptual parity, never a claim of an identical backend.

## 1. Architecture

```
Owner microphone / speakers
   ↕  realtime media transport (WebRTC preferred; WebSocket audio where WebRTC is unavailable)
Realtime Voice Provider (speech-to-speech, full-duplex, server VAD, tool calling)
   ↕  sideband: provider tool-call events ⇄ client relay ⇄ Cloud Core authenticated API
Hetzner Cloud Core — the brain: session records, tool execution, plans, memory, narration cursors
   ↕
Memory / Browser / Research / Windows Agent / Artifacts / Evolution
```

- **Direct media path.** Audio never transits Hetzner when a direct client ↔ provider
  path is lower-latency. Hetzner's role in the media path is exactly one thing: minting a
  short-lived, single-session provider credential.
- **Client-mediated sideband.** The provider delivers tool calls to the client over its
  data channel; the client relays them to Cloud Core over the existing owner-session API;
  Cloud Core executes and returns the result; the client submits it to the provider. The
  provider never holds owner authority. Cloud Core may also push sideband messages to the
  client (plan changes, narration cursor updates, "say this preamble") over the existing
  authenticated WebSocket surface.
- **Capability-driven selection.** The `ConversationRealtime` mode chooses a provider by
  declared capabilities; no model name is hardcoded. OpenAI Realtime is the first adapter.

## 2. Capability model (extends `ProviderCapabilities`)

Added fields, all declared by each adapter and asserted by tests:
`speech_to_speech: bool`, `full_duplex: bool`, `barge_in: bool`,
`end_of_turn: {"silence", "server_vad", "semantic"}`, `tool_calling: bool`,
`transports: ("webrtc", "websocket", ...)`, `ephemeral_credentials: bool`,
`input_formats` / `output_formats`, `interrupt_latency_class`. Selection for
`ConversationRealtime` requires `speech_to_speech ∧ full_duplex ∧ barge_in ∧ tool_calling
∧ tr-TR`, prefers `end_of_turn == semantic` and `webrtc`, and is otherwise ordered by the
configured preference list. Selection is a pure function with tests.

## 3. Modes (explicit capabilities)

| Mode | Backed by | Notes |
|---|---|---|
| `ConversationRealtime` | speech-to-speech provider session | the primary path; this milestone |
| `Narration` | existing narration engine + TTS adapters | durable cursor; realtime intents bridged in; speed control; chunked, never whole-document |
| `Transcription` | STT adapters | notes/meetings/videos/commands; not the conversation path |
| `VoiceIdentity` | speaker classifier | augment-only; never a sole root of authentication |

## 4. Session lifecycle (Cloud Core `app/voice/realtime_sessions`)

1. `POST /v1/voice/realtime/sessions` (owner session required, device-bound where the
   client is a device) → selects the provider by capability, mints the provider's
   ephemeral credential, records a `realtime_sessions` row (id, provider, transport,
   client kind, device id, created/expires, state, plan id, narration cursor ref),
   returns `{session_id, provider, transport, credential, tools, instructions}`.
   `instructions` is the Turkish persona + the executive-assistant defaults; `tools` is
   the manifest of Cloud Core capabilities exposed to this session.
2. Client opens the media session with the provider using the credential.
3. Tool call from the provider → `POST /v1/voice/realtime/sessions/{id}/tool-calls`
   `{call_id, name, arguments}` — idempotent on `call_id`; Cloud Core dispatches to the
   capability, records the call, returns `{call_id, result | error, preamble?}`.
   Long-running tools return immediately with `{"status": "running", "preamble": "..."}`
   and complete via the sideband push; the client submits the final output when it lands.
4. Sideband push (existing authenticated WS): `plan_changed`, `tool_progress`,
   `tool_completed`, `narration_cursor`, `say` (a short phrase Cloud Core wants spoken).
5. `POST .../sessions/{id}/events` — the client reports timing events for the benchmark
   (§8) and state transitions (barge-in, end-of-turn, intents) so Cloud Core's session
   record and audit stay authoritative.
6. Close/expire; the transcript summary and open plan are persisted for continuity
   (§7). Every step writes `voice_*` audit rows (no audio content, no credentials).

## 5. Barge-in, end-of-turn, intents

- **Barge-in**: on owner speech start while the assistant is speaking, the client stops
  local playback *first* (the latency-critical action), then cancels the provider's
  in-flight response, then reports `barge_in` with client-measured
  `playback_stopped_ms`. The M4 FSM ordering is preserved verbatim.
- **End-of-turn**: the provider's server VAD in semantic mode where available; the
  client adds a Turkish hesitation guard (configurable trailing-silence extension after
  fillers such as "şey", "yani", "hani", "ıı", trailing vowels) so normal hesitation is
  not cut off. False-barge rate is measured on a real hesitation set.
- **Intents** (resolved in Cloud Core from the transcript + session/narration state, not
  by keyword alone): `dur` (top priority, any state), `devam`, `tekrar oku`, `ikinci
  maddeyi tekrar oku` (semantic navigation into the narration plan), `biraz daha
  yavaş/hızlı` (speed), `özet geç`, `detaya gir` (presentation level), `burayı atla`
  (cursor skip). Stop words keep the M4 `STOP_WORDS` semantics.

## 6. Long-running tools while talking

A tool the manifest marks `long_running` returns a natural Turkish **preamble** at once
("Bakıyorum. OpenAI, Anthropic, Google ve önemli açık kaynak gelişmelerini
karşılaştıracağım."), the session stays live, progress may be spoken briefly, and a
redirect from the owner ("Sadece OpenAI kısmına bak") becomes a `plan_changed` on the
*same* plan — cancel-and-replan, never a disconnected conversation. Silence longer than a
configured bound during a tool is a defect the harness reports.

## 7. Continuity

The session record plus a rolling transcript summary and the open plan let a new client
(desktop → web → phone) attach with `POST .../sessions/{id}/attach`, receive the state,
and continue; the previous media leg is closed. Network loss on a client is a
`network_lost` event (M4 FSM), state retained, reattach on restore.

## 8. Benchmark harness (`app/voice/realtime_bench.py` + client timestamps)

Records, per turn, with client-side monotonic timestamps and Cloud Core correlation:
`mic_to_uplink_ms`, `eot_to_first_audio_ms`, `barge_in_to_stop_ms`,
`tool_preamble_ms`, `tool_done_to_speech_ms`, plus gap detection. Targets
(PersonalAgentOS's own, validated on the real environment): barge-in-to-stop < ~150 ms,
short-turn first audio < ~500–700 ms where technically achievable. Runs offline against the
full-duplex simulator (numbers for the gate) and for real against the owner's machine
(numbers for acceptance). Terminology and Turkish-phonetics sets from
`VOICE_TURKISH_EVAL_SET.md`, extended with the M12 list.

## 9. Security

Provider credentials: owner-provisioned, in the existing secret store, never in git; the
per-session credential is short-lived and scoped to one session. The provider gets no
owner session token. Tool execution happens only in Cloud Core under the owner session
that created the voice session; capability scope and the Authorized Asset Registry apply
unchanged. `VoiceIdentity` is augment-only. Audit rows carry ids and timings, never audio
or credentials. The qualified Windows service/companion boundary is not redesigned; the
companion's audio capability is additive.

## 10. Work breakdown (parallel tracks)

- **A. Server core** — capability model, provider selection, session service and DB
  migration, sideband tool relay, continuity, audit rows, simulator provider, tests.
- **B. OpenAI Realtime adapter** — capability declaration, ephemeral credential minting,
  request/event mapping, tool-call bridging, `end_of_turn=semantic` where the API
  offers it; unit-tested without I/O (M4's `ProviderRequest` pattern), live-tested only
  when the owner supplies the credential.
- **C. Desktop audio client** — Session Companion audio capture/playback, device
  selection, AEC/NS, WebRTC/WebSocket leg, barge-in stop-first, hesitation guard, event
  reporting; additive to the qualified binary via the deployment engine.
- **D. Web client** — browser WebRTC leg in `apps/web` for the same session contract.
- **E. Intent resolver + narration bridge + benchmark harness** — Turkish intents against
  session/narration state; the five metrics offline against the simulator.
- **F (parallel, M13 prep)** — research pipeline design against the real Windows Browser
  Agent and Chrome, with provenance labelling and executive-summary output.

Order of proof: A+E offline on the simulator → gate → B behind the abstraction → owner
credential (one action) → C on the owner's machine with the real provider → real-mic
qualification (one action) → owner quality evaluation (final gate).
