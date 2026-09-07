---
name: adr0066-speech-lifecycle
description: 2026-09-07 M18.2 DEFECT 1 fix — `speaking` is a per-response lifecycle (SpeechLifecycle on the snapshot as `speech`), response_done no longer ends it; new `audio_done` timing event; server-side acceptance of the kind was being added in parallel; owner re-run pending
metadata:
  type: project
---

On 2026-09-07 the web voice controller (`apps/web/app/lib/voice/controller.ts`) gained a
per-response `SpeechLifecycle` (ADR-0066): `response_done` is the end of GENERATION only;
`speaking` persists in phase `draining` until the provider's `audio_stopped` for that
response id, analyser silence for `PLAYBACK_RELEASE_MS` (400 ms), the
`PLAYBACK_DRAIN_MAX_MS` cap (8 s), or an interruption. Every audible response ends in one
`audio_done` event (`basis`: provider | silence | cap | interrupted | superseded).

**Why:** the owner saw the Core drop out of SPEAKING mid-sentence over WebRTC; the
mechanism was the controller leaving `speaking` on `response.done` while the media track
still held unplayed audio. The RMS analyser was a red herring (it only drives the pulse).

**How to apply:**
- Never reintroduce "response_done -> listening" as a direct transition; go through
  `finishPlayback(at, basis, settle)`; interruption paths must call it with `settle=false`.
- `audio_done` was added to the client's `TIMING_EVENT_KINDS`; the server
  (`services/api/app/voice/realtime_sessions/service.py`) skipped unknown kinds at the time
  and the kind was being added server-side in parallel — verify with grep before assuming
  the benchmark (`realtime_bench.py`) consumes it.
- Owner re-run pending: expected observation is that the Core stays `speaking` (calmer
  through pauses) until the last word and drops on "dur" as before.
- Tests that script a response must now end it (emit `audio_stopped`, or advance 400 ms of
  silence) before asserting `listening`; four older tests were changed that way.
- Related: [[m12-realtime-state]], [[adr0047-latency-state]].
