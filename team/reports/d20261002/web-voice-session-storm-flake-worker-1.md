**Şu an üzerinde çalışılan:** `web-voice-session-storm-flake` — area `apps/web/tests/voice/session-storm.test.ts` + `team/plans/web-voice-session-storm-flake-adr.md`; machine: owner's PC, worktree `worker-web-voice-session-storm-flake`. Done, pushed, tree clean, nothing left running.

**Verdict: (a) — the test waited on a timer; the product has no race here.** The failure under load is the runner's 5 s timeout, not an assertion.

**sha:** `f4f2e6cfcdfd493a3b938d7a724fa4012571367f` (pushed). 2 files changed, both inside the area.

**Cause, with the lines (base file):**
- `:35-37` `settleMacrotasks` is `setTimeout(r, 0)` per round; `:125-132` `run()` ends each call with three of them; `:280-287` the test calls `run()` fifty times.
- That is 156 real timers, about 14.8 ms each on this machine, so 2.3 s of the 5 s limit is spent idle before any load.
- Nothing was waiting for time: the controller's only clock is the injected `FakeScheduler`, and the body read is microtasks (3 turns on Node 24.15), not a macrotask as the old comment said.

**Cure:** every wait is now `answered()` — one `setImmediate` turn, repeated while a request is still on the wire. No timeout was added or lengthened, and no assertion of the seven original tests changed. The server's request and event logs for those seven tests are identical under the old and new waits.

**Before / after** — `node node_modules/vitest/vitest.mjs run --reporter=json --outputFile=<run>.json` in `apps/web`, three processes at once, ten rounds (PROVEN_AUTOMATED):

| | BEFORE (`982dc4fb`) | AFTER |
|---|---|---|
| `session-storm` red, 30 loaded full-suite runs | 1 (timeout, 5011 ms) | 0 |
| rate-limiter test duration | min 2271 / median 2365 / max 5011 ms | min 3 / median 4 / max 95 ms |
| full suite, 10 in a row | not measured | 10 of 10 green, 2106 tests |

1 of 30 is a small count; the durations are the stronger evidence.

**Tests added (2):**
- "the full incident arms no real timer": RED first against the old helpers (`expected "setTimeout" to be called +0 times, but got 156 times`), green after.
- "a wait ends when the answer has been acted on": added with the cure, so it has no RED-first run of its own; mutation M5 below turns it RED.

**Mutation RED:**
- The cure itself, `answered()` put back on `setTimeout(0)`: pin RED (51 calls); file restored, sha256 identical before and after.
- The subject is outside the area, so these ran in a scratch copy (removed); the real `controller.ts`, `events.ts`, `fake.ts`, `api.ts` sha256 are unchanged:
  - M1, a 410 no longer ends the reporter: 4 RED, including the rate-limiter test (`expected 8 to be +0`).
  - M3, the reconnect join guard removed: RED (`21` vs `2`).
  - M4, the attach single-flight removed: RED (`2` vs `1`).
  - M5, the reporter flushes on the real clock: 4 RED, both pins among them.
  - **Not RED:** M2 (the 410 announced through the reporter) and M6 (`disconnect` without dispose), 9 of 9 pass.

**Fast checks:** `tsc --noEmit` exit 0; oxlint on the file exit 0.

**Finding for the lead — a product defect, deterministic, not the flake, not asserted or papered over:**
- After a 410, a network flap takes the controller from `closed` back to `reconnecting`, and each flap posts one `attach` to the dead session.
- `controller.ts:2853-2869` and `:2788-2791` set `state: "closed"` but never `closing`; `:2739-2762` guards only on `closing` and on `reconnecting`.
- The owner is shown "reconnecting" for a dead session for as long as the network is down.
- The rate-limiter test's server log has four attaches after the 410; the test counts only `/events` posts, so it stays green.
- Probe output and a proposed card are in the ADR text.

**Not done / NOT_RUN:**
- `quality-gate.ps1`: NOT_RUN (only the web steps were run directly).
- The inspector's "7 of 60 after `web-voice-test-flakes`" state was not rebuilt; this branch's base is main without that merge.

**Open risks:**
- Other timeouts in the same loaded runs, outside the area: `cockpit/quiet-families` 3 and `preview/core-preview` 1 (7–8.7 s, no card that I know of); `voice/latency` 1 before and `voice/store` 1 after (both cured by ADR-0251, not yet on this base).
- `answered()` counts a request as answered when the fake's promise settles; if a later Node reads bodies across several turns, the second pin fails deterministically rather than flaking.
