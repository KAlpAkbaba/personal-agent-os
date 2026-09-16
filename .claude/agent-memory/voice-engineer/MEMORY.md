# Memory Index

- [M12 realtime voice state](project_m12_realtime_state.md) — A+E+B done 2026-09-02; C/D open; simulator dev-only gate; owner credential step = realtime_smoke.py
- [M12 noise gate state](project_m12_noise_gate.md) — ADR-0044 web mic/noise package shipped 2026-09-02; Layer 4 (provider VAD) server-side only; owner actions pending
- [ADR-0047 latency state](project_adr0047_latency_state.md) — 2026-09-03 K66-driven client optimisation; stop_cmd_ms is forbidden (pcm); owner rerun pending
- [ADR-0063 action receipts state](project_adr0063_action_receipts.md) — 2026-09-06 Cloud Core half + same-day amendment: receipts carry session_id/observed_at/action_trace, GET /v1/state/now; owner re-run pending
- [M18 eye action seam](project_m18_eye_action_seam.md) — 2026-09-06 the tool is the ONLY eye mutation path (utterance safety net removed after session 3eb6fee7); verified rests on `local.changed`; truthful "kapandı ancak kaydını doğrulayamadım"
- [M18 eye generation race](project_m18_eye_generation_race.md) — 2026-09-06 session 9df439af: transitions are gen-owned; `stopLocalIfStale` dated-bus rule; `stopLocalOnly` gone; docs pass owed
- [Worktree pnpm gates](reference_worktree_pnpm_gates.md) — fresh worktree has no node_modules; one offline install first, never two pnpm exec in parallel
- [ADR-0063 action receipts state](project_adr0063_action_receipts.md) — 2026-09-06 Cloud Core half shipped: state.now/eye.*/release.promote, receipts; owner re-run pending
- [M18 eye action seam](project_m18_eye_action_seam.md) — 2026-09-06 EyeStore does the durable POST itself; `eye__disable` vendor spelling normalised only in controller; 9-class error set + track state + action_trace
- [M18 Core voice surface](project_m18_core_voice_surface.md) — ADR-0061 2026-09-06: one VoiceStore per tab, local overlay rules, no query_kind yet, owner /core run pending
- [ADR-0066 speech lifecycle](project_adr0066_speech_lifecycle.md) — 2026-09-07 M18.2 defect 1: `speaking` persists through `draining`; `audio_done` event; server kind added in parallel; owner re-run pending
- [TTS loopback proxy](project_tts_loopback_proxy.md) — 2026-09-07 branch voice-loopback: real baseline 38/2/0/0 WER 0.018; marks fixed; -Post needs a Core release
- [B47 device voice](project_b47_device_voice.md) — 2026-09-16 continuous listening; M12 client streamed silence; no offline Turkish engine, DTW templates
