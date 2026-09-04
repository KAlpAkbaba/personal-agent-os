# Memory Index

- [M3 verification outcome](project_pagentos_m3.md) — M3 PASS, DB reset gotcha, PS console mangles Turkish, task/artifact body-exposure contract, one coverage gap
- [M6 verification outcome](project_pagentos_m6.md) — M6 PASS via live adversarial CLI/API drive; BUILD_STATE.json not auto-updated by gate; MSYS taskkill //PID gotcha
- [M8 verification outcome](project_pagentos_m8.md) — M8 PASS on re-verify (8b7812a/1288917): registry wiring confirmed source+runtime+live HTTP 5-case grant matrix; junction fix and breadth guard reproduced independently; pass-1 PARTIAL history kept for the lesson
- [M9 verification outcome](project_pagentos_m9.md) — M9 closeable at 610f8c4; identity/mobile fully proven; standing dev-Postgres connection-exhaustion defect in subprocess-killing E2E tests (not M9 code), quantified and root-caused
- [M12+M13 verification outcome](project_pagentos_m12_m13.md) — 69ae5b3: 10/11 claims PROVEN via mutation/adversarial probes; real audit-scrub bypass found (apiKey/api-key not caught by api_key substring match)
- [Arbor voice + M12 noise-gate verification](project_pagentos_arbor_m12_noise.md) — a969375/f34cfd6 PROVEN via mutation probes; real coverage gap in reattach-dedup claim; forbidden-key parity independently re-executed client vs server
- [Concurrent-agent working tree hazard](feedback_concurrent_agent_worktree.md) — git status can drift mid-session from another agent's edits; re-check before reporting, don't trust only the start snapshot
- [ADR-0047 voice latency/echo-gate verification](project_pagentos_adr0047_voice_latency.md) — PASS 6/6 claims via mutation probes; real gap: server BREAKDOWN_FIELDS/timing_breakdown does not exist anywhere in repo
