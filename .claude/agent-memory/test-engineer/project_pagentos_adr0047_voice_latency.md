---
name: project-pagentos-adr0047-voice-latency
description: ADR-0047 voice-client latency/echo-gate verification outcome (commits 967472d+efb48b6) — PASS with one real premise gap in the server timing-breakdown claim
metadata:
  type: project
---

ADR-0047 (measured latency decomposition, reversible early mute, echo-aware
self-learning gate, non-sentinel calibration) on worktree
`agent-a69ad9636bb22b6bd`, commits 967472d + efb48b6: verified PASS on all six
claims via mutation probes against the real TypeScript (vitest + throwaway
git-tracked mutations, reverted after each). apps/web test 120/120, lint 0
errors, build green.

Mutation probes performed (each: patch source in the worktree with `node -e`
or `sed`, run the targeted vitest file, confirm the expected test(s) fail,
`git checkout --` to restore — worktree left clean):
- Disabled the gate's `evidence` emission (gate.ts) → robustness.test.ts's two
  playback-consistency tests fail as expected; but latency.test.ts's mute
  test does NOT fail (it drives the controller through `FakeSpeechDetector`,
  not the real gate) — the two test files are complementary, not redundant.
- No-op'd `onLocalEvidence` (controller.ts) → both latency.test.ts early-mute
  tests fail as expected.
- Reverted `echoMarginFor` to the old constant +6 dB (calibration.ts) → 3
  robustness.test.ts tests fail, including the false-opens-zero synthetic-set
  proof.
- No-op'd `settleUplink` (controller.ts) → exactly the network_lost-unmatched
  case in the "never silent loss" test fails (the false_start/provider_first
  cases have their own inline settle path, so only the network-loss branch
  broke — correctly localized).
- Forced `anomaly = 0` unconditionally in `bargeIn` → the provider-first
  0 ms anomaly test fails as expected.

**Real gap found**: the verification brief asserted the server has a
`service.py BREAKDOWN_FIELDS` that "picks up" the client's fine-grained
breakdown keys (`gate_ms`, `capture_lag_ms`, `rtp_ms`, `provider_ms`,
`detect_ms`, `stop_command_ms`, `gain_zero_ms`, `response_created_ms`,
`first_delta_ms`, `playback_ms`) into a `timing_breakdown`. No such constant
or concept exists anywhere in the repo (`grep -rn "BREAKDOWN_FIELDS\|timing_breakdown"`
across the whole tree returns nothing). `realtime_bench.py` only computes
event-to-event deltas by `(kind, t_ms)` pairs (`mic_to_uplink_ms` etc.); it
never parses the sub-field breakdown by name — those ride along as opaque
payload values, scrubbed only by `is_forbidden_key`. This matches ADR-0047's
own stated scope ("the server's aggregation ... is not changed here"), so the
implementation is not at fault — the verification brief's premise about a
named `BREAKDOWN_FIELDS` was simply wrong. **How to apply**: when a
verification brief asserts a specific named symbol exists server-side, grep
for it directly before trusting the premise — briefs written by an
orchestrating agent can embed unverified assumptions about the codebase.

Independently re-ran `is_forbidden_key` (server, via uv) and `isForbiddenKey`
(client) against the full ADR-0047 key list plus `stop_cmd_ms`/`apiKey`/
`api-key`: both reject only the intended spellings, both accept every real
ADR-0047 key, confirming [[project_pagentos_m12_m13]]'s api_key scrub fix is
still in force and the two implementations stay in lockstep.

See also [[project_pagentos_arbor_m12_noise]] for the prior noise-gate
verification this one builds on, and [[feedback_concurrent_agent_worktree]]
for the worktree-hygiene practice followed here (git status clean before and
after every mutation).
