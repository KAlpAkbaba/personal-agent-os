---
name: m12-realtime-voice-state
description: M12 realtime voice — what tracks A+E delivered on 2026-09-02, what remains (B adapter, C/D clients), and the standing rules (simulator = gate only, acceptance real-only)
metadata:
  type: project
---

M12 tracks A (server core) and E (intents + harness) were implemented on 2026-09-02 on
branch `worktree-agent-ae5268b8d02ff769c` (committed there, not pushed). The
ConversationRealtime path is: `app/voice/selection.py` (pure capability selection),
`app/voice/simulator.py` (deterministic full-duplex provider = the gate), the
`app/voice/realtime_sessions` package (migration 0011, `/v1/voice/realtime/...`),
`app/voice/intents.py` (Turkish intents -> narration cursor/speed), and
`app/voice/realtime_bench.py` (five latency metrics; targets never claims).

**Why:** ADR-0034 forbids STT->LLM->TTS as the primary conversation and requires
capability-driven provider selection; ADR-0035 records the reversible choices
(shared `audit_events` table for `voice_*` rows, `call_id` unique per session, media
leg owned by one owner session at a time, "ikinci madde" counts non-heading items).

**How to apply:** track B (real speech-to-speech adapter, e.g. OpenAI Realtime) must
register in `RealtimeVoiceRuntime.providers` and win by declared capabilities, never
by name; tracks C/D implement create -> media -> tool relay -> events -> attach.
Simulator numbers are gate evidence only; acceptance numbers come from the owner's
machine via `GET /v1/voice/realtime/sessions/{id}/benchmark`. The owner credential
ask is deferred until the adapter exists behind the abstraction. The integration test
`tests/integration/test_voice_realtime_sessions.py` needs the compose stack at head.
