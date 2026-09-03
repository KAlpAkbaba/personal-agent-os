---
name: adr-0047-realtime-latency-state
description: ADR-0047 (2026-09-03) web voice client optimisation from the owner's real K66 numbers — root causes, payload contract, what only the owner's rerun can confirm
metadata:
  type: project
---

ADR-0047 shipped 2026-09-03 on worktree branch `worktree-agent-a69ad9636bb22b6bd` (commit on top of e67b43b, not pushed): measured latency decomposition, reversible early mute, echo-aware + self-learning gate, non-sentinel calibration. Server (`realtime_bench.py`, `service.py`) untouched; the coordinator said the server-side `context.breakdown` aggregation on their side expects exactly the payload names in the ADR.

**Why:** the owner's closed K66 session measured barge-in 210 ms (= pre-roll 120 + onset 70 + poll 20, self-inflicted), mic→uplink 391 ms (never measured the wire; 6 samples lost silently), and a persisted calibration with `peak_db=-100` (a dead all-zero window that became "the room").

**How to apply:**
- Payload key `stop_cmd_ms` is FORBIDDEN (normalises to "stopcmdms" → contains "pcm"); use `stop_command_ms`. Sweep every new payload key through `isForbiddenKey` in a test.
- Barge-in / uplink latencies as reported include the gate's pre-roll + onset by construction; the honest transport component is `capture_lag_ms + rtp_ms`.
- Only the owner's real rerun can confirm: capture lag / RTP cadence on the K66, barge-in < 150 ms on the real audio thread, where the 925/1059 ms EOT outliers land (`response_created_ms` vs `first_delta_ms`), the K66 echo residual and false-barge-in count, no dead calibration window after `prepare()`.
- Related: [[m12-realtime-voice-state]], [[m12-noise-gate-state]].
