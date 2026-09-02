# Voice gap analysis — M4 against the realtime standard (2026-09-02)

The standard (ADR-0034, `ACCEPTANCE_TESTS.md` §M12): ChatGPT-Voice-class conversation
through a native realtime speech-to-speech provider behind a capability-driven
abstraction, direct media path, Hetzner as the brain over a sideband, explicit modes,
measured latency, real-only acceptance. This document says what M4 already gives that
standard, what it does not, and what is replaced versus extended. Nothing here is a
claim about voice quality — that is measured later, on the owner's machine.

## What M4 built (surveyed from `services/api/app/voice`, `app/narration`, `docs/VOICE_SPEC.md`)

| Component | Where | Verdict |
|---|---|---|
| `ProviderCapabilities` (name, kind tts/stt/realtime, languages, streaming, latency class, cost, formats, key required) + `TTSProvider` / `STTProvider` / `RealtimeProvider` protocols + `ProviderRouter` with fallback | `voice/providers.py`, `voice/router.py` | **Keep, extend.** The abstraction is the right shape; its realtime vocabulary is too thin (see gaps). |
| Deterministic fakes (`FakeTTSProvider`, `FakeSTTProvider`, `FakeRealtimeProvider`) | `voice/providers.py` | **Keep for TTS/STT; rework the realtime fake** — today it ignores audio and only drives the control FSM. |
| Real adapters: ElevenLabs / Azure / OpenAI TTS, OpenAI / Azure / faster-whisper STT, request-building without I/O | `voice/providers.py` | **Keep.** These serve the `Narration` and `Transcription` modes. None is a speech-to-speech conversation path. |
| `RealtimeSession` control FSM: IDLE/LISTENING/ASSISTANT_SPEAKING/TOOL_RUNNING/INTERRUPTED/CLOSED, stop-word priority (dur/kes/sus/yeter), progress-while-tool-runs, network lost/restored, ordered event log, barge-in latency in events | `voice/realtime.py` | **Keep as the coordination model.** It is correct about ordering ("cut speech first, then latch") and about stop words. It carries no audio, no transport, no timing in milliseconds — those are the gaps, not this. |
| Narration engine: `NarrationPlan` (section/paragraph/chunk), `Cursor`, `next_cursor`/`next_section_cursor`, `ChunkCache`, `Synthesizer` protocol, commands FSM (oku/dur/devam/tekrar), sessions persisted, pronunciation entries | `narration/*` | **Keep.** This is the `Narration` mode, already durable-cursor and chunked (never pre-synthesises the document). Needs the realtime intents bridged into it. |
| Turkish normaliser, number engine, table narration | `narration/normalizer.py`, `numbers.py`, `tables.py` | **Keep.** Applies to narration and to any text the provider is asked to speak verbatim. |
| Speaker classifier OWNER / NOT_OWNER / UNCERTAIN, encrypted derived embeddings, device-trust required | `voice/speaker.py`, `voice/crypto.py` | **Keep** as `VoiceIdentity`, augment-only — never a sole root of authentication (standing M4 rule, restated in ADR-0034). |
| Preferences, benchmark harness (TTS/STT cases), Turkish eval set | `voice/preferences.py`, `voice/benchmark.py`, `docs/VOICE_TURKISH_EVAL_SET.md` | **Keep, extend.** The harness measures batch synth/transcribe latency; it does not measure the five realtime metrics. |

## What is missing for the standard

1. **No native speech-to-speech conversation path.** The only "realtime" is a protocol
   and a fake; every real adapter is TTS or STT. The primary path required by ADR-0034
   does not exist.
2. **No audio transport and no client audio at all.** No WebRTC or WebSocket audio
   session anywhere; `apps/web`, `clients/` and the Windows Session Companion contain no
   `getUserMedia`/`RTCPeerConnection`/audio capture or playback code. Microphone
   switching, AEC, noise suppression, headset/laptop/phone mics are client concerns and
   have no home yet.
3. **Capability vocabulary cannot express the choice.** `ProviderCapabilities` has no
   `speech_to_speech`, `full_duplex`, `barge_in`, `server_vad` / `semantic_end_of_turn`,
   `tool_calling`, `transports`, `ephemeral_credentials`, so "select the best realtime
   provider by capability" cannot be written today.
4. **No sideband tool channel.** Nothing mints per-session provider credentials, nothing
   relays provider tool calls to Cloud Core, nothing returns results. The M4 FSM has
   `TOOL_RUNNING`, but no tool is ever actually called through a voice session.
5. **End-of-turn is silence-shaped or absent.** Semantic end-of-turn, hesitation
   tolerance for Turkish ("şey…", "yani…", trailing vowels), and false-barge measurement
   do not exist.
6. **Turkish intents beyond stop words are not bridged.** devam / tekrar oku / ikinci
   maddeyi tekrar oku / biraz daha yavaş / hızlı / özet geç / detaya gir / burayı atla
   exist partly as narration commands (`narration/commands.py`) but are not resolved
   from a live conversation against narration/session state.
7. **No session continuity.** No realtime-session record, no resumable state, no
   handoff between desktop, web and phone.
8. **No realtime latency measurement.** mic → uplink, end-of-turn → first audible,
   barge-in → playback stopped, tool preamble, tool completion → resumed speech are not
   instrumented; "barge-in latency" today is a count of FSM events.
9. **Long-running tools have no voice protocol.** No preamble, no keepalive of the
   session during a 30-second research job, no mid-task redirection that changes the
   active plan instead of starting a new conversation.

## What is replaced, what is extended

- **Extended, not replaced:** `ProviderCapabilities` (realtime fields), `RealtimeProvider`
  (the session handle grows audio in/out, events, tool-call plumbing and timing), the
  benchmark harness (the five realtime metrics), the narration engine (intent bridge and
  speed control from the conversation), the eval set (terminology and hesitation sets).
- **Reworked:** `FakeRealtimeProvider` becomes a deterministic full-duplex *simulator*
  with controllable timings and synthetic audio frames, so barge-in, end-of-turn,
  preamble and latency logic are testable offline and the harness produces numbers
  before any real provider is wired.
- **New:** the realtime session service in Cloud Core (credential minting, session
  record, sideband tool relay, continuity), the first real speech-to-speech adapter
  (OpenAI Realtime, selected by capability), a desktop audio client in the Session
  Companion (the owner-session process that already owns UI and audio by design), a web
  WebRTC client, the Turkish intent resolver, and the latency harness.
- **Untouched:** everything qualified in RQ-1/RQ-2. The Session Companion gains an audio
  capability as an addition to the qualified binary through the normal deployment
  engine; the service/companion IPC, DACLs and admission are not redesigned.

## Owner dependencies, batched (none due yet)

A realtime provider credential once the adapter exists behind the abstraction and the
simulator path passes the gate; a real-microphone Turkish session for qualification;
subjective voice-quality evaluation as the final gate.
