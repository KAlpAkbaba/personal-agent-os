---
name: m12-realtime-voice-state
description: M12 realtime voice — tracks A+E (2026-09-02) and track B OpenAI Realtime adapter (2026-09-02) delivered; C/D clients open; standing rules (simulator = dev-only gate, acceptance real-only, owner credential step = realtime_smoke.py)
metadata:
  type: project
---

M12 tracks A (server core) and E (intents + harness) were implemented on 2026-09-02
(merged to main as 69ae5b3). Track B — the first REAL native speech-to-speech adapter,
OpenAI Realtime — was implemented on 2026-09-02 on branch
`worktree-agent-a9b309ae36e7461d5` (committed there, not pushed; ADR-0037).
The ConversationRealtime path is: `app/voice/selection.py` (pure capability selection),
`app/voice/simulator.py` (deterministic gate), `app/voice/providers_openai_realtime.py`
(real adapter: credential mint + transport descriptor + event/command mapping), the
`app/voice/realtime_sessions` package (`/v1/voice/realtime/...`), `app/voice/intents.py`
and `app/voice/realtime_bench.py`.

**Why:** ADR-0034 forbids STT->LLM->TTS as the primary conversation and requires
capability-driven provider selection; ADR-0036/0037 record the reversible choices.
Track A flagged that the simulator could answer a production session if the real
adapter lacked its key — hence the simulator is a candidate only in `environment=dev`
or behind `PAGENTOS_VOICE_REALTIME_SIMULATOR_ENABLED=true`.

**How to apply:** the vendor's Turkish quality for the conversational model is
UNVERIFIED (survey `docs/research/realtime-providers-2026-09.md` §1.5) — never claim
it; it is measured on the owner's machine. The owner credential step is now unblocked:
`PAGENTOS_VOICE_OPENAI_API_KEY=... uv run python scripts/realtime_smoke.py` (exit 3 =
key absent). Its scrubbed `session_echo` confirms the GA `audio.input/output` field
names — check it before building the C/D clients' audio pipelines on the adapter's
assumed shape. Tracks C/D open the media leg from `credential.transport_descriptor`
and reuse `map_server_event` / `barge_in_commands` / `tool_result_commands` semantics.
Simulator numbers are gate evidence only; acceptance numbers come from
`GET /v1/voice/realtime/sessions/{id}/benchmark`. Azure Voice Live is the documented
second-adapter candidate if measured Turkish quality is insufficient. The integration
test `tests/integration/test_voice_realtime_sessions.py` needs the compose stack.
