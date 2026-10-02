# Inspector report — web-voice-session-storm-flake (`f4f2e6cf`, base `982dc4fb`)

Cause (a) is confirmed: the test waited on real timers, the cure removes them, and nothing was weakened. All runs were mine, from `apps/web` on Node 24.15, with the tree clean at start and end.

**Pass 1 — re-run (PROVEN_AUTOMATED)**
- Command: `node node_modules/vitest/vitest.mjs run --reporter=json --outputFile=<run>.json`, three processes at once, ten rounds, for both rows below.

| | BEFORE (base test file swapped in) | AFTER |
|---|---|---|
| `session-storm` red, 30 loaded runs | **8** (rate-limiter test, runner timeout, 5010–6020 ms) | **0** |
| rate-limiter test duration | min 2289 / median 2448 / max 6020 ms | min 3 / median 4 / max 99 ms |

- The worker's BEFORE count was 1 of 30; mine is 8 of 30, in line with the earlier inspector's 7 of 60.
- Full suite, 10 serial runs: 10 of 10 green, 2106 tests each.
- File alone: 9 of 9, 31 ms of tests.
- `tsc --noEmit` exit 0; oxlint on the file exit 0.
- The commit touches only the two area files: no product file, no dependency, no timeout added or lengthened. The seven original tests' assertions are byte-identical.
- I recorded the server's request log and event log for the seven original tests under the old and the new waits: identical, request for request and event for event.

**My mutations** (in place, one at a time, restored from backup; sha256 of `controller.ts`, `events.ts` and the test equal before and after)
- **B1**, `events.ts`, a 410 handled like a 429 (bounded backoff): 3 RED, including the rate-limiter test (`expected 4 to be 1`).
- **B2**, `controller.ts`, the 410 no longer records `closed`: 2 RED (`'error'` vs `'closed'`).
- **B3**, `controller.ts`, the back-online edge calls `runReattachLoop` directly: RED (`21` vs `2`).
- **B4**, `events.ts`, failure sinks notified one macrotask late: the "a wait ends…" pin goes RED (`'listening'` vs `'closed'`), so a reaction that slips past one turn fails deterministically.
- **A**, test side, `answered()` with the `while (wire.open > 0)` loop removed: **not RED, 9 of 9**.

**Pass 2 — findings (none blocking)**
1. **The `wire.open` loop is not load-bearing.** Every fake answer is already settled, so one `setImmediate` turn always suffices and the loop never repeats. The docstring ("the loop is the event itself") and ADR decision 1 overstate it; the ADR text should say so at numbering.
2. **The cause of the flake is not in this file alone.** Other files timed out the same way under load:
   - BEFORE runs: `cockpit/quiet-families` 1, `eye/transition-race` 1, `pages/discoverability` 1, `voice/latency` 1.
   - AFTER runs: `voice/latency` 2 (timeouts) and `voice/store` 2 (an assertion, `'closed'` vs `'listening'`); ADR-0251 covers both and is not on this base.
   - One cold, unloaded full run: `features/matrix-badges` and `pages/discoverability` (6.3 s and 6.7 s).
   - `quiet-families`, `transition-race`, `discoverability` and `matrix-badges` have no card that I know of, and they will redden the gate's web step under load.
3. **The worker's product finding is confirmed.** The rate-limiter test's request log is `contract, sessions, attach, events, attach, attach, attach, attach`.
   - The first attach is already against the dead session, so that is five attaches to a gone session, not four.
   - The 410 on that first attach does not end the reporter: the `/events` POST after it still goes out. The card for `controller.ts` should cover both.
   - The test counts only `/events`, so it stays green before and after alike; nothing was papered over.
4. **Minor:** `wire` is one module-level counter reset in `beforeEach`. A request still open at a test's end would drive it negative and shorten the next test's wait. It cannot happen with today's fakes.
5. No secrets, paths, privacy or contract surface is touched. Rollback is reverting one test file.

**NOT_RUN**
- `quality-gate.ps1`: task branch, not integration, and the diff is test-only; I ran the web step directly.
- PostgreSQL and real infrastructure: not applicable, the diff touches no table, store or container.
- The "7 of 60 after `web-voice-test-flakes`" state was not rebuilt; that merge is not on this base.

**Evidence class:** PROVEN_AUTOMATED for the before/after counts, the mutations and the log equivalence.

APPROVE
