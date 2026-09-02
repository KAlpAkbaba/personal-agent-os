# Realtime speech-to-speech provider research — 2026-09-02

Scope: M12 realtime voice foundation (`docs/M12_REALTIME_VOICE_SPEC.md`, ADR-0034 in
`docs/DECISIONS.md`). This document supports building the `ConversationRealtime`
capability's provider adapters, starting with OpenAI Realtime. It covers exact API
surface, turn detection, tool calling, language/voice, client SDKs, pricing and a
capability-field mapping for provider selection.

**Method.** Facts are sourced from vendor-official documentation, fetched and
cross-checked on 2026-09-02, with verbatim quoting where feasible to reduce
summarization drift. Every factual claim below carries an inline citation. Anything I
could not confirm against an official source is explicitly marked **UNVERIFIED**.
Recommendations (as opposed to vendor facts) are called out as **Recommendation:**.

**Headline caveat for this whole document.** OpenAI's realtime docs moved from
`platform.openai.com/docs/...` to `developers.openai.com/api/docs/...` (301 redirects
observed live during this research) — treat `platform.openai.com` realtime links found
elsewhere as stale mirrors of the new canonical location. Where the automated fetch
tool summarized rather than quoted a page, I re-fetched with an explicit
"quote verbatim" instruction and used that version; a few fields (marked below) were
only obtainable via search-engine snippets of the official page and should be
double-checked against the live doc immediately before implementation, since realtime
APIs are moving targets (OpenAI shipped a beta→GA migration with breaking changes in
just the last few months per its own changelog).

---

## 1. OpenAI Realtime (primary candidate per ADR-0034)

### 1.1 API surface — sessions, transports, ephemeral credentials

Three transports are documented, each for a different client shape:

- **WebRTC** — "for browser and mobile clients that capture or play audio directly."
- **WebSocket** — "for when your server already receives raw audio from a media
  pipeline, call system, or worker."
- **SIP** — "for telephony voice agents."

[Realtime and audio — overview](https://developers.openai.com/api/docs/guides/realtime)
(canonical; redirects from `platform.openai.com/docs/guides/realtime`).

**WebRTC connection (browser/desktop-with-embedded-webview clients):**
- SDP exchange endpoint: `POST https://api.openai.com/v1/realtime/calls` — the browser
  posts a multipart body (`sdp` = the browser's SDP offer, `session` = a JSON session
  config) and receives the SDP answer as plain text, which the client sets as its remote
  description.
- The events data channel must be named **`oai-events`**; it carries JSON-serialized
  client/server events (session config, `response.cancel`, tool-call events, etc.).
- Standard WebRTC flow otherwise: create `RTCPeerConnection`, attach mic track,
  `ontrack` for inbound audio, create the `oai-events` data channel, create/set local
  SDP offer, POST it, set the returned answer as remote description; ICE negotiates
  automatically.
[Realtime API with WebRTC](https://developers.openai.com/api/docs/guides/realtime-webrtc).

- **Ephemeral client credential minting:** `POST https://api.openai.com/v1/realtime/client_secrets`
  (called from your own backend, using your standing OpenAI API key — never expose the
  standing key to a browser/device client). Request body:
  - `session`: either a `RealtimeSessionCreateRequest` (`type: "realtime"`, the normal
    voice-agent config: `model`, `audio` object for format/turn_detection/transcription,
    `instructions`, `output_modalities`, `tools`, `tool_choice`, `max_output_tokens`,
    `parallel_tool_calls`, `tracing`, `truncation`, `prompt`) or a
    `RealtimeTranscriptionSessionCreateRequest` (`type: "transcription"`, input-only).
  - `expires_after`: `{ anchor: "created_at" (only supported value), seconds: 10–7200,
    default 600 }` — i.e. **default TTL 10 minutes, minimum 10 seconds, maximum 2
    hours**, anchored at creation.
  - Response: `{ value: "ek_<hex>...", expires_at: <unix seconds>, session: {...} }`.
    The returned secret can start **one call/session**; the guide's phrasing is that the
    secret is what a browser or mobile client uses to establish its own session — treat
    it as single-session/single-use scoping for our credential-minting design (M12 §4:
    "mints the provider's ephemeral credential ... scoped to one session").
  [Create client secret reference](https://developers.openai.com/api/reference/resources/realtime/subresources/client_secrets/methods/create).
  - **Caveat, UNVERIFIED precisely:** community reports (OpenAI developer forum) describe
    a real-world discrepancy where the *usable* ephemeral token lifetime observed in
    practice was much shorter (~60s) than the configured `expires_after`, for the
    original beta ephemeral-key flow. This may not apply to the current GA
    `client_secrets` endpoint. **Recommendation:** the adapter must not assume the full
    configured TTL is usable and should mint the credential immediately before the
    client opens the media session, and treat any expiry-related connection failure as
    retryable (mint fresh, reconnect) rather than as a hard error.

- **Session vs. secret create endpoint naming:** there is also a `sessions` create
  resource (`POST /v1/realtime/sessions` in the pre-GA docs); in the current API surface
  session configuration is passed as part of the `client_secrets` create call's `session`
  field, and a session object is echoed back with an assigned `id` and
  `object: "realtime.session"` / `"realtime.transcription_session"`.
  [Create session reference](https://developers.openai.com/api/reference/resources/realtime/subresources/sessions/methods/create).

**WebSocket connection (server-side clients, e.g. a Cloud Core relay or a telephony
bridge):** connect to `wss://api.openai.com/v1/realtime` (endpoint per the overview
guide) using the standing API key directly (server-held secret, not the browser
ephemeral flow) — appropriate when "your server already receives raw audio from a media
pipeline." [Realtime and audio — overview](https://developers.openai.com/api/docs/guides/realtime).
Per ADR-0034/M12 §1 our default is the **direct client↔provider WebRTC path**; the
WebSocket transport is the fallback for any client that cannot do WebRTC, and is also
what a Windows desktop client not using an embedded browser control might use if a
native WebRTC library is undesirable (see §1.6).

**SIP (telephony, background/utility — not part of the primary desktop/browser
conversation path, noted for completeness):**
- Region endpoints: `sip:$PROJECT_ID@sip.api.openai.com;transport=tls` (US) and
  `sip:$PROJECT_ID@sip-eu.api.openai.com;transport=tls` (EU).
- Requires a webhook (configured in the OpenAI project) to receive incoming calls, a SIP
  trunk provider (e.g. Twilio) pointed at the OpenAI SIP endpoint, `Authorization: Bearer
  $OPENAI_API_KEY` on the control-plane calls, webhook signature verification via a
  `webhook-signature` header, outbound TCP/TLS 5061 for signaling and bidirectional SRTP
  over UDP to OpenAI's documented CIDR ranges.
  [Realtime API — SIP](https://developers.openai.com/api/docs/guides/realtime-sip).

**Audio formats/sample rates:**
- Supported formats: `pcm16`, `g711_ulaw`, `g711_alaw`.
- `pcm16`: 16-bit PCM, mono, little-endian, **24 kHz** sample rate (input and output).
  24 kHz is the only sample rate documented for realtime audio.
  [Session create reference / VAD guide, cross-checked](https://developers.openai.com/api/reference/resources/realtime/subresources/sessions/methods/create).
  **Recommendation:** confirm the exact field names (`input_audio_format`/
  `output_audio_format` live under the session's `audio` sub-object in the GA schema,
  per the client_secrets schema summary above) against a live `session.created` event
  before wiring the desktop client's capture/playback pipeline, since the API migrated
  from a flatter beta schema to a nested `audio.input`/`audio.output` shape at GA.

### 1.2 Turn detection

`turn_detection` (nested under the session's `audio.input` in the GA schema) has two
mutually exclusive `type` values:

**`server_vad`** (silence-based):
- `threshold` — activation threshold 0.0–1.0, default **0.5**; higher requires louder
  audio, better in noisy rooms.
- `prefix_padding_ms` — audio to include before detected speech start, default **300**.
- `silence_duration_ms` — trailing silence required to call end-of-speech, default
  **500**; shorter → faster turn detection.
- `create_response` / `interrupt_response` — booleans, "only in conversation mode" (i.e.
  not meaningful in a transcription-only session).
[Voice activity detection (VAD)](https://developers.openai.com/api/docs/guides/realtime-vad).

**`semantic_vad`** — this is OpenAI's semantic end-of-turn mode, directly relevant to
M12's `end_of_turn == "semantic"` preference and the Turkish hesitation-guard design:
"uses a semantic classifier to detect when the user has finished speaking, based on the
words they have uttered." Tuned by:
- `eagerness`: `"low" | "medium" | "high" | "auto"` — `auto` (default) ≡ `medium`;
  `low` "will let the user take their time to speak"; `high` "will chunk the audio as
  soon as possible." In transcription-only mode it still affects chunking even without a
  spoken reply.
- `create_response` / `interrupt_response` — same semantics as server_vad.
[Voice activity detection (VAD)](https://developers.openai.com/api/docs/guides/realtime-vad).

**Recommendation:** map M12's `end_of_turn: semantic` capability flag to
`turn_detection.type = "semantic_vad"`, default `eagerness: "low"` for the Turkish
hesitation guard (M12 §5) so normal "şey/yani/hani" hesitation isn't chunked away, and
have the intent resolver override to `"high"` for short-command-style turns if measured
false-barge / latency tradeoffs justify it later.

Turn detection can also be turned off entirely (`turn_detection: null`) for manual
push-to-talk control, or run with automatic VAD but `create_response`/
`interrupt_response` both `false` to get speech-boundary events without auto-generation
— useful for the FSM's "detect but don't yet commit" states. [Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations).

### 1.3 Interruption / barge-in

- With VAD enabled, the **server automatically truncates unplayed audio when the user
  interrupts**; the client still owns local playback.
- Server event `input_audio_buffer.speech_started` fires when user speech is detected —
  this is the trigger the client uses to stop local playback *first* per M12 §5 ("client
  stops local playback first, then cancels the provider's in-flight response").
- Client sends **`response.cancel`** to cancel an in-flight response explicitly (used in
  push-to-talk / manual-VAD flows, and as the second step of the M12 barge-in sequence).
- Server confirms cancellation with a **`response.cancelled`** event.
- **WebRTC/SIP clients**: send **`output_audio_buffer.clear`** to clear unplayed audio
  server-side (in addition to conversation truncation).
- **WebSocket clients** (no server-managed audio buffer) must track how much of the
  model's last audio response was actually played, and send
  **`conversation.item.truncate`** `{item_id, content_index, audio_end_ms}` to tell the
  server to drop the unplayed tail from conversation history — otherwise the model's
  context would include audio the user never heard.
[Realtime conversations — interruptions](https://developers.openai.com/api/docs/guides/realtime-conversations).

**Timing events usable for latency measurement** (feeds the M12 §8 benchmark harness):
`input_audio_buffer.speech_started` (candidate origin for `barge_in_to_stop_ms` — client
timestamps its own local-playback-stop against this), `response.cancelled` (provider-side
cancel confirmation), `conversation.item.created`/`conversation.item.done` (turn
boundaries), and the function-call event pair in §1.4 (for `tool_preamble_ms` /
`tool_done_to_speech_ms`). **UNVERIFIED**: I did not find an OpenAI-documented
"first audio byte" event distinct from the WebRTC media track itself — for
`eot_to_first_audio_ms`, the adapter will need to timestamp the first inbound RTP audio
frame on the client side rather than relying on a JSON event; confirm no better signal
exists before implementation (check `response.output_audio.delta`-equivalent events in
the GA event list).

### 1.4 Tool / function calling

- Streaming args: server event **`response.function_call_arguments.delta`** streams
  partial JSON argument text as the model generates a call.
- Completion: **`response.done`** carries the full response, including a
  `function_call`-typed output item with `name`, `arguments` (JSON string), and
  `call_id`.
- Submit the result: client sends **`conversation.item.create`** with
  `{"type": "function_call_output", "call_id": "<call_id>", "output": "<json-string>"}`.
- Resume generation: client sends **`response.create`** after submitting the output —
  "This will trigger a model response using the data from the function call."
[Realtime function calling](https://developers.openai.com/api/docs/guides/realtime-function-calling).

**Mid-flight submission / preamble:** the official guide's documented pattern is
wait-for-`response.done` → execute → submit `function_call_output` → `response.create`.
It does **not** document submitting a function output while a *different* response is
still in flight, nor a first-class "speak while the tool runs" primitive — **UNVERIFIED**
against the live event reference (this page's tool-calling section did not enumerate
that case). M12 §6 needs the model to speak a short Turkish preamble immediately and
then continue after a long tool finishes. **Recommendation**, consistent with the M12
design (which already puts the preamble in Cloud Core's control, not the provider's):
have Cloud Core's initial tool response for a `long_running`-flagged tool return
`{"status": "running", "preamble": "..."}` per M12 §4 step 3, and have the **client**
speak that preamble via a `conversation.item.create` (a synthetic assistant text/audio
turn) or a `response.create` with a short fixed instruction, rather than depending on the
provider to interleave tool latency with speech on its own. Do not build the adapter
around an assumption that OpenAI will "keep talking" during a pending tool call — that
must be verified live before it's load-bearing; treat it as UNVERIFIED capability, not a
default.

### 1.5 Language, voices, transcription, output modes

- **Voice options (current, GA):** `alloy`, `ash`, `ballad`, `coral`, `echo`, `sage`,
  `shimmer`, `verse`, `marin`, `cedar`. The guide recommends `marin` or `cedar` "for
  optimal quality." [Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations).
  **UNVERIFIED**: no per-voice Turkish quality notes were found in official docs; voice
  quality in Turkish should be A/B tested per M12 §8/ADR-0034 §6 (real-only acceptance)
  rather than assumed from the voice name/description.
- **Explicit Turkish statement:** OpenAI's realtime prompting guide instructs the model
  not to infer the user's language from accent alone and to default to a configured
  language unless the user explicitly speaks another one — this is behavioral guidance
  about a multilingual model, not an enumerated supported-language list.
  [Realtime models & prompting](https://developers.openai.com/api/docs/guides/realtime-models-prompting).
  A **broader model-language claim exists but only for the separate `gpt-realtime-translate`
  model** (a realtime *translation*-specific model, not the general conversational
  `gpt-realtime`): "more than 70 input languages and 13 output languages" for one-way
  translation, and Turkish appears in the wider auto-detect input list for that
  translation feature.
  [Build Live Translation Apps with gpt-realtime-translate](https://developers.openai.com/cookbook/examples/voice_solutions/realtime_translation_guide).
  **Conclusion: OpenAI does not publish an explicit "Turkish is supported/at what
  quality" statement for the primary conversational `gpt-realtime` model** — treat
  Turkish conversational quality as **UNVERIFIED until measured** against the real
  environment per ADR-0034 §6, which is already this project's stated acceptance gate.
- **Input audio transcription models** (`input_audio_transcription.model` field,
  independent of the response voice): `whisper-1`, `gpt-transcribe`, `gpt-live-transcribe`,
  `gpt-4o-mini-transcribe` (and dated snapshot `gpt-4o-mini-transcribe-2025-12-15`),
  `gpt-4o-transcribe`, `gpt-4o-transcribe-diarize` (adds speaker labels),
  `gpt-realtime-whisper`. Additional transcription config fields: `language`,
  `languages` (plural — used specifically by `gpt-live-transcribe`, which "uses
  `languages` instead of the singular `language` field" and accepts ISO 639-1 codes plus
  selected ISO 639-3 codes and regional Chinese locale codes), `keywords`, `prompt`,
  `delay`. Transcript events: **`conversation.item.input_audio_transcription.delta`**
  (incremental) and **`conversation.item.input_audio_transcription.completed`** (final).
  [Realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription);
  model/field list cross-checked via the [session create reference](https://developers.openai.com/api/reference/resources/realtime/subresources/sessions/methods/create).
  `tr` (ISO 639-1) should be usable as a `language`/`languages` hint for
  Whisper-family transcription models — **UNVERIFIED** for exact behavior/accuracy with
  `gpt-live-transcribe` specifically; test against real Turkish speech per the M12
  acceptance gate.
- **Output modalities:** session-level `output_modalities`: `["audio"]` (default when
  audio is wanted), `["text"]`, or `["audio","text"]`; overridable per-`response.create`
  call. [Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations).

### 1.6 Client SDKs — .NET and browser

- **.NET/C#**: `openai/openai-dotnet` is the official OpenAI .NET library, built "in
  collaboration with Microsoft." **Stable/GA Realtime REST API support landed in
  v2.9.0, dated 2026-02-27** ("Added support for the stable version of the Realtime
  REST API"), with a `RealtimeClient` class in the `OpenAI.Realtime` namespace; the GA
  migration included breaking changes versus the earlier beta client, and the repo
  includes a migration guide.
  [openai/openai-dotnet CHANGELOG](https://github.com/openai/openai-dotnet/blob/main/CHANGELOG.md),
  [openai/openai-dotnet repository](https://github.com/openai/openai-dotnet). Install via
  `dotnet add package OpenAI`. [Official libraries list](https://developers.openai.com/api/docs/libraries).
  **Recommendation for the M12 Track B/C adapter**: use `OpenAI.Realtime.RealtimeClient`
  from the .NET 10 Session Companion for the **WebSocket** transport (server/native
  client pattern) rather than reimplementing raw WebSocket JSON framing — this is
  consistent with ADR-0015's .NET 10 target. If WebRTC is wanted directly from the
  Windows desktop client (lower latency than a WS relay) there is **no official .NET
  WebRTC realtime SDK**; that would mean either a native WebRTC library (e.g.
  Microsoft's `Microsoft.MixedReality.WebRTC`/`SIPSorcery`-class libraries — not OpenAI
  products, **UNVERIFIED** for this project's needs) or routing WebRTC through an
  embedded browser control. **Recommendation:** start the desktop client on the
  WebSocket transport via `OpenAI.Realtime.RealtimeClient` (official, supported,
  simplest to get correct) and treat WebRTC-from-desktop as a later latency
  optimization, matching M12 §1's "WebRTC preferred; WebSocket audio where WebRTC is
  unavailable."
- **Browser/JS**: the official package for building realtime voice agents in the browser
  is **`@openai/agents-realtime`** (`npm i @openai/agents-realtime`, part of the
  `openai/openai-agents-js` repo), exposing `RealtimeAgent`/`RealtimeSession`; a
  `RealtimeSession` configured for the browser "automatically connects your microphone
  and audio output ... via WebRTC." There is also a lower-level `OpenAIRealtimeWebRTC`
  transport class in the same package for custom wiring.
  [Realtime Agents Quickstart](https://openai.github.io/openai-agents-js/guides/voice-agents/quickstart/),
  [`@openai/agents-realtime` on npm](https://www.npmjs.com/package/@openai/agents-realtime).
  This is the right fit for Track D (`apps/web` browser WebRTC leg).
- Raw-protocol fallback (any client): the WebRTC SDP/`client_secrets` flow and the
  `oai-events` data-channel JSON events are fully documented and usable without any SDK
  — relevant if the Session Companion or web client ever needs to bypass the SDKs for
  control.

### 1.7 Pricing, rate limits, session length

- **`gpt-realtime` pricing (official, per 1M tokens):** audio input **$32.00**, cached
  audio input **$0.40**, audio output **$64.00**; text input **$4.00**, cached text
  **$0.40**, text output **$16.00**; image input **$5.00**, cached image **$0.50** (no
  image output line). [OpenAI Realtime & audio pricing](https://developers.openai.com/api/docs/pricing).
  Token-to-time conversion is documented elsewhere at roughly 1 audio token per 100 ms
  of *input* speech and 1 per 50 ms of *output* speech for the flagship model
  (i.e. ~600 input tokens/min, ~1200 output tokens/min) — this specific ratio came from
  a third-party cost breakdown, not an OpenAI page I could directly quote; treat the
  **token prices above as verified**, the **tokens-per-second-of-audio conversion as
  UNVERIFIED**, and compute actual per-minute cost from real session token usage once
  the owner's credential is live (per ADR-0034 §7, credential is deferred until the
  adapter exists).
- **Session length**: OpenAI's own developer blog states plainly: **"Realtime sessions
  can now last up to 60 minutes, up from 30 minutes."**
  [Developer notes on the Realtime API](https://developers.openai.com/blog/realtime-api)
  — no explicit publish date on the page itself, but it references the
  `gpt-realtime-2025-08-28` model snapshot, placing the increase at/after the August
  2025 GA launch. **Recommendation:** Cloud Core's `realtime_sessions` continuity design
  (M12 §7 — reattach a new client leg to the same session record) should treat 60
  minutes as the provider-side hard ceiling per connection and plan reattachment/renewal
  before that boundary for any conversation the owner keeps open longer.
- **Concurrency / rate limits**: no concurrent-session cap is documented on the current
  official rate-limits page for Realtime specifically (the page covers general
  tokens-per-minute/requests-per-minute tiers, not a realtime-specific table).
  **UNVERIFIED** via official source for a hard concurrency ceiling — for a single-owner
  assistant with (at most) a handful of simultaneous device sessions this is unlikely to
  matter, but do not assume unlimited concurrency without checking the account's actual
  tier limits at deploy time. [Rate limits](https://developers.openai.com/api/docs/guides/rate-limits).

### 1.8 Model identity

- Model ID: `gpt-realtime`; default snapshot `gpt-realtime-2025-08-28`; 32,000 token
  context window; 4,096 max output tokens; input modalities text/audio/image; output
  modalities text/audio; October 2023 knowledge cutoff.
  [`gpt-realtime` model page](https://developers.openai.com/api/docs/models/gpt-realtime).
- A faster/cheaper `gpt-realtime-mini` variant and versioned snapshots
  (e.g. `gpt-realtime-2.1`, referenced directly in OpenAI's own SIP guide example) also
  exist in the current lineup; **do not hardcode a specific dated snapshot** in the
  adapter — M12's capability-driven selection principle (§1: "no model name is
  hardcoded") already matches OpenAI's own recommended practice of pointing at the
  unversioned `gpt-realtime` alias or an explicitly chosen snapshot per deployment.

---

## 2. Other native speech-to-speech / full-duplex candidates

### 2.1 Google Gemini Live API

Genuinely native audio-to-audio for its "native audio" model family (as opposed to a
"half-cascade" mode that does native audio-in but classic TTS audio-out).
[Live API models](https://ai.google.dev/gemini-api/docs/models).

- **Models:** `gemini-3.1-flash-live-preview` ("audio-to-audio (A2A)... real-time
  dialogue") and `gemini-2.5-flash-native-audio-preview-12-2025` ("flagship Live API
  model for low-latency, bidirectional voice and video agents with native audio
  reasoning"). [Live API models](https://ai.google.dev/gemini-api/docs/models).
- **Transport:** stateful WebSocket (WSS); GenAI SDK (Python and others) or raw
  WebSocket from JS/browser. [Get started with Live API](https://ai.google.dev/gemini-api/docs/live).
- **Ephemeral tokens:** `POST https://generativelanguage.googleapis.com/v1beta/auth_tokens`
  (or `client.auth_tokens.create()` in SDKs). Fields: `uses` (default token usage count;
  `uses: 1` restricts a token to starting a single session), `expireTime` (overall token
  validity — **default 30 minutes** in the future), `newSessionExpireTime` (window in
  which a *new* session may be started with this token — **default 1 minute** in the
  future), `liveConnectConstraints` (locks the token to a specific session config).
  Session-resumption tokens (separate from auth tokens) allow reconnecting to a live
  session within the `expireTime` window roughly every 10 minutes.
  [Ephemeral tokens](https://ai.google.dev/gemini-api/docs/ephemeral-tokens). This is a
  materially tighter default "new session" window than OpenAI's 10-minute default
  `client_secrets` TTL — worth noting if Gemini Live is ever added as a fallback
  provider: the credential-minting step must be very close in time to session start.
- **Audio formats:** input raw 16-bit PCM, **16 kHz**, little-endian; output raw 16-bit
  PCM, **24 kHz**, little-endian — asymmetric in/out sample rates, unlike OpenAI's
  symmetric 24 kHz. [Get started with Live API](https://ai.google.dev/gemini-api/docs/live).
- **Turn detection / VAD:** `realtimeInputConfig.automaticActivityDetection`, enabled by
  default. Parameters: `startOfSpeechSensitivity` / `endOfSpeechSensitivity` (LOW/HIGH),
  `prefixPaddingMs`, `silenceDurationMs` (commonly tuned 400–600 ms). No documented
  **semantic** end-of-turn mode analogous to OpenAI's `semantic_vad` — Gemini Live's VAD
  is described purely in silence/sensitivity terms in the official docs I could reach;
  **UNVERIFIED** whether a semantic mode exists elsewhere in the API surface (some
  third-party/community sources report inconsistent behavior of `silenceDurationMs` on
  at least one preview model — treat as a live-test item, not a blocking fact).
  Automatic VAD can be disabled (`automaticActivityDetection.disabled = true`) for
  client-driven manual turn boundaries via explicit `activityStart`/`activityEnd`
  messages. [Live API WebSockets reference, via search-verified official content](https://ai.google.dev/api/live).
- **Interruption:** server signals cancellation via an `interrupted` field inside
  `serverContent`; the client is responsible for stopping local playback on that signal.
  [Live API session guide](https://ai.google.dev/gemini-api/docs/live-session)
  (session-management specifics), cross-referenced with the live-guide page.
- **Tool calling:** `BidiGenerateContentToolCall` server message; client responds with
  `FunctionResponse` objects via `session.send_tool_response`. Async scheduling control
  per tool response — `scheduling: "INTERRUPT" | "WHEN_IDLE" | "SILENT"` — directly
  supports M12 §6's "preamble, then keep going" pattern:
  `WHEN_IDLE` lets the model finish its current utterance before reporting a tool
  result, which is the closest documented first-class analogue among all providers
  researched to "speak now, resolve tool later." [Live API tool use](https://ai.google.dev/gemini-api/docs/live-tools).
- **Session length:** **audio-only sessions limited to 15 minutes without context-window
  compression**; **audio+video limited to 2 minutes**; underlying connection lifetime is
  roughly 10 minutes before the transport itself terminates. Context-window compression
  (a sliding-window token-threshold mechanism) can extend effective session length.
  Session-resumption tokens remain valid **2 hours** after last session termination, to
  support reconnect-and-continue. [Live API session management](https://ai.google.dev/gemini-api/docs/live-session).
  This is a materially shorter native ceiling than OpenAI's 60 minutes and would need
  the M12 continuity/reattach machinery (§7) exercised far more aggressively if Gemini
  Live were the active provider.
- **Language:** an official supported-languages table lists **97 languages by BCP-47
  code, including `tr` (Turkish)**. [Live API guide — supported languages](https://ai.google.dev/gemini-api/docs/live-guide).
  Voice sets differ by model type: half-cascade models are limited to a fixed roster
  (Puck, Charon, Kore, Fenrir, Aoede, Leda, Orus, Zephyr per third-party comparison,
  **UNVERIFIED** against an official enumeration I could directly quote); native-audio
  models draw on Google's broader TTS voice catalog. Whether Turkish is at full quality
  specifically on the *native audio* models (vs. only the half-cascade/TTS layer) is
  **UNVERIFIED** — flagged directly by a Google Cloud doc's own advice to "test quality
  for your specific use case" per language.
- **Pricing:** official per-token rate **$1.00/1M audio input tokens, $20.00/1M audio
  output tokens** on the Gemini Developer API pricing page, at a documented ~1,500
  tokens/minute of audio (25 tokens/sec) — giving roughly **$0.0015/min input,
  $0.03/min output**, i.e. meaningfully cheaper than `gpt-realtime`'s
  $32/$64 per 1M. [Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing)
  (rate figures cross-checked via search-engine snippet of that same official page; spot
  the exact rate on the live page before relying on it for budgeting).
- **SDKs:** official Python and JS/TS ("GenAI SDK"); **no official .NET/C# SDK found** —
  a .NET client would go through the raw WebSocket protocol. **UNVERIFIED** whether a
  community C# wrapper is production-appropriate; not evaluated here.

**Recommendation:** Gemini Live is a credible secondary adapter (cheaper, `tr` officially
listed, async tool scheduling has a cleaner "keep talking" primitive than OpenAI's
documented flow) but its 15-minute unaugmented session ceiling and lack of a documented
semantic-VAD mode make it a worse fit for M12's "prefers `end_of_turn == semantic`"
selection rule than OpenAI Realtime. Good candidate for the provider preference list,
not the default.

### 2.2 Amazon Nova Sonic / Nova 2 Sonic (AWS Bedrock)

- **Architecture:** genuinely unified speech-to-speech model family via Bedrock's
  bidirectional streaming API (`InvokeModelWithBidirectionalStream`), event-driven JSON
  frames over a persistent stream — session init, continuous audio streaming in,
  concurrent ASR-transcript/tool-use/text/audio-chunk events out.
  [Amazon Nova Sonic (v1) speech guide](https://docs.aws.amazon.com/nova/latest/userguide/speech.html).
- **Nova 2 Sonic** (current generation, referenced as a December 2025 release by
  third-party trackers — **UNVERIFIED** exact GA date from an AWS-first-party page) adds
  automatic language detection/switching, and lists supported languages as:
  **English (US, UK, India, Australia), French, Italian, German, Spanish, Portuguese,
  Hindi.** [Amazon Nova 2 Sonic speech-to-speech guide](https://docs.aws.amazon.com/nova/latest/nova2-userguide/using-conversational-speech.html).
  **Turkish is not in this list — Nova Sonic / Nova 2 Sonic does not officially support
  Turkish**, which disqualifies it against M12's `∧ tr-TR` selection requirement
  regardless of its other capabilities.
- **Session limit:** explicitly documented **8-minute connection limit**, with a
  "connection renewal and session continuation pattern" available in AWS's own code
  samples — i.e. AWS expects callers to reconnect roughly every 8 minutes and treats that
  as normal operation, not an edge case. [Amazon Nova 2 Sonic guide](https://docs.aws.amazon.com/nova/latest/nova2-userguide/using-conversational-speech.html).
- **Turn detection / barge-in:** described qualitatively ("intelligent turn-taking,"
  "graceful handling of user interruptions without dropping conversational context") —
  no documented numeric VAD parameters (threshold/silence-ms) were found on the pages
  reached. **UNVERIFIED** for exact configurability.
- **Tool calling:** "function calling and agentic workflow support," with "asynchronous
  tool handling that executes tool calls while maintaining conversation flow, allowing
  the assistant to continue speaking while tools process in the background" — this is
  the same "speak while a tool runs" property M12 §6 wants, described qualitatively but
  without the specific event-name-level detail (`toolUse`/`toolResult`-style names) I
  could directly quote from an AWS page in this pass. **UNVERIFIED** exact event
  schema — needs a direct read of `docs.aws.amazon.com/nova/latest/nova2-userguide/speech-tools.html`
  before implementation.
- **Pricing:** Nova 2 Sonic audio pricing reported (via aggregator, not an AWS-first-party
  price page I could directly quote) at **~$3.40/1M audio input tokens, ~$13.60/1M audio
  output tokens** — notably cheaper than `gpt-realtime`. **Mark pricing UNVERIFIED**
  against AWS's own pricing page before any cost decision.
- **SDK:** AWS SDKs (boto3 etc.) via Bedrock's bidirectional streaming client;
  **no dedicated realtime-voice SDK** beyond the general Bedrock runtime SDKs.

**Recommendation:** exclude Nova Sonic from the M12 provider preference list as currently
documented — no Turkish support is a hard disqualifier per the capability-selection rule
in M12 §2, independent of its otherwise-attractive pricing and native async tool
handling. Revisit only if AWS adds Turkish.

### 2.3 ElevenLabs Conversational AI

- **Architecture: NOT native speech-to-speech.** ElevenLabs' own overview describes an
  explicitly cascaded pipeline: "a fine-tuned Speech to Text (ASR) model," "your choice
  of language model," and "a low-latency Text to Speech (TTS) model across 5k+ voices
  and 70+ languages." [Conversational AI overview](https://elevenlabs.io/docs/conversational-ai/overview).
  This directly fails ADR-0034's "primary conversation path is a native realtime
  speech-to-speech provider" requirement and M12's `speech_to_speech: bool` capability
  gate — ElevenLabs Conversational AI should declare `speech_to_speech: false` in any
  adapter and therefore cannot satisfy `ConversationRealtime` selection, however good its
  voices are.
- **Transports:** raw WebSocket (`wss://api.elevenlabs.io/v1/convai/conversation?agent_id=...`)
  is the documented low-level protocol; a signed URL is minted via
  `GET https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?agent_id=...`
  with API-key auth (**TTL not stated on the page reached** — UNVERIFIED); WebRTC is
  available but runs over LiveKit under the hood rather than a directly-documented raw
  SDP exchange, per LiveKit's own integration docs.
  [WebSocket protocol](https://elevenlabs.io/docs/conversational-ai/libraries/web-sockets),
  [ElevenLabs × LiveKit integration](https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-engine/livekit-integration).
  SIP trunking / Twilio telephony integration is also documented.
- **Turn-taking / interruption:** "a proprietary turn-taking model" with configurable
  "turn-taking, interruptions, and timeout settings" — no numeric VAD parameters
  surfaced on the pages reached. **UNVERIFIED** in detail.
- **Events (from the WebSocket protocol doc):** `user_transcript` (ASR result),
  `agent_response` (agent text), `interruption` (with a reason field),
  `agent_response_correction` (revised response after interruption). Tool-calling is
  supported ("call clients & APIs to perform actions"; contextual updates become tool
  calls in conversation history) but I could not directly quote a discrete
  tool-call/tool-result event pair from the pages reached — **UNVERIFIED** exact names.
- **Language:** 70+ languages advertised platform-wide for TTS/voices; Turkish voices
  exist in the general ElevenLabs voice catalog (including regional-accent variants),
  and the "Eleven v3 Conversational" model is marketed for low-latency expressive
  conversational delivery. No official document found that scores Turkish
  conversational-AI *turn-taking* quality specifically (as opposed to standalone TTS
  quality). **UNVERIFIED** for conversational-mode Turkish quality specifically.
- **SDKs:** React, Swift (iOS), Kotlin (Android), React Native are the documented
  official SDKs; **no .NET/C# SDK** and no bare browser-JS SDK beyond the React wrapper
  and the raw WebSocket protocol were found.
- **Pricing (official, `elevenlabs.io/pricing/agents`):** **$0.08/min** standard call
  rate, **$0.16/min** burst rate when exceeding a plan's concurrency limit, **$0.003**
  per text message; included minutes and concurrency scale by plan (Free: 15 min / 4
  concurrent; Starter: 75 min / 6; Creator: 275 min / 10; Pro: 1,238 min / 20; Scale:
  3,738 min / 30; Business: 12,375 min / 40).
  [ElevenAgents pricing](https://elevenlabs.io/pricing/agents).

**Recommendation:** do not use ElevenLabs Conversational AI as the `ConversationRealtime`
provider — it is architecturally a cascaded pipeline, not the native speech-to-speech
path ADR-0034 mandates. It remains directly relevant to PersonalAgentOS as an
**already-planned TTS provider for the separate `Narration` mode** (M4/ADR-0022 lists
ElevenLabs among the TTS adapter skeletons) — keep that use distinct from
`ConversationRealtime`, per the Voice rule in `CLAUDE.md` ("do not conflate ... realtime
assistant voice [and] long-form narration TTS").

### 2.4 Azure AI Foundry Voice Live API (Microsoft)

Included because it is a genuinely distinct *native* speech-to-speech offering when
paired with an `azure-realtime`/`gpt-realtime*` model, not merely an Azure-hosted mirror
of OpenAI's API — and because it adds turn-detection and language features OpenAI's own
API does not have.

- **What it is:** a managed, WebSocket-based (and separately, WebRTC-based) speech-to-
  speech interface that is protocol-compatible with the Azure OpenAI Realtime API event
  set, but layers Azure-specific extensions: noise suppression, server-side echo
  cancellation, "advanced end-of-turn detection," avatar output, and a curated
  `azure-realtime` native-voice model in addition to hosting OpenAI's own `gpt-realtime`
  family and several non-realtime chat models paired with cascaded Azure STT/TTS.
  [Voice Live API overview](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live).
  **Speech-to-speech is native only when the selected backing model is itself native**
  (`gpt-realtime`, `gpt-realtime-mini`, `gpt-realtime-1.5`, `azure-realtime`); models like
  `gpt-4o`/`gpt-4.1`/`phi4-mini` are explicitly cascaded ("+ audio input through Azure
  speech to text + audio output through Azure text to speech").
- **Endpoint:** `wss://<resource>.services.ai.azure.com/voice-live/realtime?api-version=2026-04-10&model=<model>`
  (or `.cognitiveservices.azure.com` for older resources); a separate WebRTC path exists
  ("Voice Live API with WebRTC" is Microsoft's recommendation for client-side web/mobile
  apps). Auth: Microsoft Entra ID bearer token (recommended,
  `https://ai.azure.com/.default` scope) or an `api-key` header/query param.
  [How to use the Voice Live API](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-how-to).
- **Turn detection — genuinely differentiated from OpenAI's own two modes:**
  `turn_detection.type` supports `server_vad` (volume-based, OpenAI-compatible),
  `semantic_vad` (OpenAI's own semantic classifier, usable only with `gpt-realtime`/
  `gpt-realtime-mini`), **and Azure-specific `azure_semantic_vad` /
  `azure_semantic_vad_multilingual`** — semantic-meaning-based turn detection usable
  with **any** backing model (not just the two OpenAI realtime models), with an explicit
  `remove_filler_words` option (English filler list documented: `ah, umm, mm, uh, huh,
  oh, yeah, hmm`) aimed at exactly the false-barge-on-hesitation problem M12 §5 names.
  Multilingual filler/semantic support (`azure_semantic_vad_multilingual`'s `languages`
  field) officially covers English, Spanish, French, Italian, German, Japanese,
  Portuguese, Chinese, Korean, Hindi — **Turkish is not in that specific filler-word
  language list**, though Azure Speech-to-text itself covers `tr-TR` broadly for
  transcription. Other documented parameters: `threshold` (default 0.5),
  `prefix_padding_ms` (400 for `server_vad` / 420 for the azure_semantic types as of API
  version 2026-04-10), `speech_duration_ms` (200 / 80 respectively),
  `silence_duration_ms` (default 500), `interrupt_response` (default true, azure types
  only), `auto_truncate` (default false), `eagerness` (semantic_vad only, OpenAI-style
  low/medium/high/auto). [How to use the Voice Live API — turn detection table](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-how-to).
- **Transcription models available:** `azure-speech` (default for non-multimodal
  models), `mai-transcribe` (Microsoft's own, preview), and — when the backing model is
  `gpt-realtime`/`gpt-realtime-mini` — the same OpenAI transcription models as §1.5
  (`whisper-1`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `gpt-4o-transcribe-diarize`).
- **Voices / Turkish:** Azure's general TTS voice catalog officially lists two Turkish
  standard neural voices — **`tr-TR-AylinNeural`** (female) and **`tr-TR-BalciNeural`**
  (male) — per Microsoft's own language-support table.
  [Language and voice support for Azure Speech](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts).
  (Third-party voice catalogs additionally list older `tr-TR-EmelNeural`/
  `tr-TR-AhmetNeural` names — **UNVERIFIED/possibly deprecated**; treat Microsoft's own
  current table as authoritative and re-check at implementation time.) The curated
  `azure-realtime-native` low-latency voice roster (the dedicated `azure-realtime` model)
  does **not** currently include a Turkish-locale voice in the enumerated list — Turkish
  on Azure Voice Live today means the general Azure Speech Turkish voices layered onto a
  non-`azure-realtime` model (e.g. `gpt-realtime` + `tr-TR-AylinNeural`), not the
  lowest-latency native voice path.
- **Pricing model:** tiered by backing model (**Pro**: `gpt-realtime` family, `gpt-4o`,
  `gpt-4.1`, `gpt-5`/`gpt-5-chat`; **Basic**: `-mini` variants; **Lite**: `gpt-5-nano`,
  `phi4-mm-realtime`, `phi4-mini`), billed on the same audio-token model as Azure OpenAI
  (~10 tokens/sec input, ~20 tokens/sec output for Azure OpenAI-family models), with
  custom voice/avatar billed separately. [Voice Live API overview — pricing](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live).
- **SDKs:** official Python (`azure.ai.voicelive`), with C#, JavaScript and Java samples
  referenced in Microsoft's quickstart — **this is the one candidate in this survey with
  an official first-party C#/.NET sample path**, worth noting given ADR-0015's .NET 10
  target, though it was only confirmed as "quickstart samples," not necessarily a
  full first-class NuGet SDK — **UNVERIFIED** exact NuGet package maturity; check
  `azure-sdk-for-net` before relying on it.

**Recommendation:** Azure Voice Live is worth keeping on the radar as a **fallback
adapter that can wrap the same `gpt-realtime` model family with Azure's
filler-word-aware semantic VAD**, and it is the only researched provider that pairs
native speech-to-speech with an explicit official Turkish TTS voice *and* a
Microsoft-documented C#/.NET sample path. It adds a second cloud dependency
(Azure account/credential, per CLAUDE.md's "ask only for paid account/credential
creation" rule) and its `azure_semantic_vad_multilingual` filler-removal doesn't
officially cover Turkish yet — so it should not replace OpenAI Realtime as the day-one
default, but is a reasonable second capability-declared adapter if OpenAI's own Turkish
quality (still UNVERIFIED per §1.5) turns out to be insufficient on real-owner testing.

### 2.5 Hume EVI (Empathic Voice Interface)

- **Architecture:** native speech-to-speech / speech-language model, distinguished by
  prosody-aware generation (tone/rhythm/timbre-sensitive responses) and its own
  proprietary end-of-turn detection guided by vocal tone rather than text semantics or
  silence alone. Sessions run over a WebSocket connection, authenticated by API key or
  access token as query parameters. [EVI overview](https://dev.hume.ai/docs/empathic-voice-interface-evi/overview).
- **Interruption:** documented as "always interruptible" — stops immediately on user
  speech and preserves context.
- **Language: no Turkish.** EVI 3 is English-only; **EVI 4-mini** (the current
  multilingual model) supports English, Japanese, Korean, Spanish, French, Portuguese,
  Italian, German, Russian, Hindi, Arabic — **Turkish is absent**, which disqualifies
  Hume EVI against M12's `tr-TR` selection requirement the same way it disqualifies Nova
  Sonic.
- **Session limits:** maximum session duration **30 minutes**; concurrency "defined by
  subscription tier"; HTTP rate limit 100 req/s (control-plane, not the realtime stream
  itself).
- **SDKs:** Python, TypeScript, Next.js documented; **no .NET SDK**.
- Ephemeral token TTL and tool-calling event names were **not found/UNVERIFIED** on the
  pages reached in this pass.

**Recommendation:** exclude from the preference list — no Turkish support, same as Nova
Sonic.

---

## 3. Capability-field comparison table

Mapped onto the `ProviderCapabilities` extension fields defined in
`docs/M12_REALTIME_VOICE_SPEC.md` §2. "Y/config" = supported and configurable; "Y
(qual.)" = supported only as a qualitative vendor claim without documented parameters
(UNVERIFIED in detail); "—" = not applicable/not offered; "N" = documented as absent.

| Field | **OpenAI Realtime** (`gpt-realtime`) | Google Gemini Live | Amazon Nova (2) Sonic | ElevenLabs Conv. AI | Azure Voice Live (on `gpt-realtime`) | Hume EVI (4-mini) |
|---|---|---|---|---|---|---|
| `speech_to_speech` | Y (native) | Y (native, "native audio" models) | Y (native) | **N — cascaded ASR→LLM→TTS** | Y (native, when backing model is native) | Y (native) |
| `full_duplex` | Y | Y | Y | Y (cascaded, still bidirectional) | Y | Y |
| `barge_in` | Y — server truncates on interrupt; client stop-first + `response.cancel` | Y — `interrupted` field in `serverContent` | Y (qual., "graceful interruption handling") | Y — `interruption` event | Y — `interrupt_response` flag, filler-word-aware | Y — "always interruptible" |
| `end_of_turn` | `silence` (`server_vad`) or **`semantic`** (`semantic_vad`, `eagerness` low/medium/high/auto) | `silence` (`automaticActivityDetection`, sensitivity+padding+silence-ms); no documented semantic mode | `silence`-class only (qual., no documented params) | proprietary "turn-taking model" (undocumented params) | `silence` (`server_vad`), **`semantic`** (`semantic_vad`, OpenAI-only models), or Azure's own **`azure_semantic_vad`/`azure_semantic_vad_multilingual`** (broader model coverage, English-centric filler removal) | proprietary prosody-based end-of-turn (undocumented params) |
| `tool_calling` | Y — `response.function_call_arguments.delta/done` → `conversation.item.create(function_call_output)` → `response.create` | Y — `BidiGenerateContentToolCall` → `send_tool_response`, with `INTERRUPT`/`WHEN_IDLE`/`SILENT` scheduling | Y — async tool handling while speaking (qual.; exact event names UNVERIFIED) | Y (qual.; exact event names UNVERIFIED) | Y — same as OpenAI when backed by `gpt-realtime` | UNVERIFIED |
| `transports` | WebRTC, WebSocket, SIP | WebSocket | Bedrock bidirectional-stream (HTTPS/gRPC-class, not WebRTC/plain-WS) | WebSocket, WebRTC (via LiveKit), SIP/Twilio | WebSocket, WebRTC | WebSocket |
| `ephemeral_credentials` | Y — `POST /v1/realtime/client_secrets`, default TTL 600 s (10 s–2 h) | Y — `POST /v1beta/auth_tokens`, default TTL 30 min (new-session window 1 min) | UNVERIFIED (Bedrock uses AWS SigV4/IAM credentials, not a documented short-lived voice-session token) | Y — signed URL via `get-signed-url` (TTL UNVERIFIED) | Y — Entra ID bearer token (short-lived, standard Azure AD token lifetime) or API key | UNVERIFIED (access token mechanism present, TTL UNVERIFIED) |
| tr-TR support | **UNVERIFIED / not explicitly documented** for the conversational model (translation-only model separately claims 70+ languages incl. Turkish) | **Y — `tr` officially listed** among 97 supported languages | **N — not in the documented language list** | Y (qual., 70+ languages platform-wide; conversational-mode Turkish quality UNVERIFIED) | **Y — official `tr-TR-AylinNeural`/`tr-TR-BalciNeural` voices**; STT via Azure Speech tr-TR broadly | **N — not in EVI 4-mini's language list** |
| input/output formats | `pcm16` (24 kHz, mono, LE), `g711_ulaw`, `g711_alaw` | in: PCM16 16 kHz LE; out: PCM16 24 kHz LE | UNVERIFIED (not directly quoted from an AWS page reached) | UNVERIFIED (WS base64 audio; exact PCM spec not directly quoted) | Configurable `input_audio_sampling_rate` 16000/24000; output via Azure TTS/OpenAI audio codecs | UNVERIFIED |
| session length | **60 minutes** (per-connection) | **15 min audio-only** (uncompressed); 2 min audio+video; resumable | **8 minutes** (documented reconnect pattern) | UNVERIFIED (plan-based minute allotments, not a documented single-connection cap) | Inherits backing model's limits (60 min for `gpt-realtime`) | **30 minutes** |

---

## 4. Recommendation summary for the adapter build order

1. **Build the OpenAI Realtime adapter first**, exactly as ADR-0034 already decided. Its
   API surface is the most completely documented of the group, it is the only one with
   both a documented semantic-VAD mode *and* a 60-minute session ceiling (least
   reattachment churn), and it has official SDKs for both the browser (`@openai/agents-realtime`)
   and .NET (`OpenAI.Realtime.RealtimeClient`, GA since v2.9.0).
2. **Treat conversational-mode Turkish quality as unverified for every provider** — none
   of the vendors publish an explicit "Turkish: supported at X quality" claim for their
   *conversational* (as opposed to translation-only or plain-TTS) product. This is
   already consistent with ADR-0034 §6's "real-only acceptance" gate; the benchmark
   harness (M12 §8) is the actual source of truth here, not vendor docs.
3. **Keep Azure Voice Live as the most plausible fallback/second adapter**, specifically
   because it is the only researched provider offering an official Turkish TTS voice
   *and* a documented semantic-VAD-class turn-detection mode usable with the same
   `gpt-realtime` family — i.e. it could reuse most of the OpenAI event-mapping code
   with a different transport/auth layer.
4. **Exclude Nova Sonic and Hume EVI from the capability preference list as currently
   documented** — both structurally fail the `tr-TR` requirement in M12 §2's selection
   rule.
5. **Exclude ElevenLabs Conversational AI from `ConversationRealtime` entirely** — it is
   cascaded, not native, by ElevenLabs' own description. Its existing role as a planned
   `Narration`/TTS provider (ADR-0022) is unaffected and should stay separate per the
   Voice rule in `CLAUDE.md`.
6. **Do not hardcode any dated model snapshot** in the adapter (consistent with M12 §2's
   capability-driven selection) — resolve `gpt-realtime` (or a specific pinned snapshot
   chosen deliberately, e.g. for reproducible benchmark runs) via configuration.
7. Before writing the client's audio pipeline, get one live `session.created`/
   `session.updated` event from the real API and confirm the exact nested
   `audio.input`/`audio.output` field names for format and turn_detection — the GA
   schema differs from the older beta flat schema, and several of the fields above were
   confirmed at the guide/prose level but not from a raw JSON schema dump.
