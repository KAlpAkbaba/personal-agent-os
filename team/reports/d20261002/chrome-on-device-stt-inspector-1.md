**Inspector report: chrome-on-device-stt @ `9b4974e2` (cycle d20261002)**

The feature behaves as the card asks in every case I ran, but three claims are not pinned by any test, so this goes back.

**Pass 1 — re-run from a clean tree**
- Five local/STT suites: 88/88. Full web suite: 124 files, 2129 passed. `tsc --noEmit` exit 0. oxlint exit 0, no warnings in the new files (three existing `on…=` warnings in `localMode.ts`). PROVEN_AUTOMATED.
- `test_voice_local_mode.py`: 9 passed. A scratch copy that posts `payload: {stt_engine}` also passed 9/9 on the unchanged server, so the value is accepted (and dropped until the lead's line). PROVEN_AUTOMATED.
- The commit touches only the area. No table, migration, store or infra, so the Postgres and host-snapshot rules do not apply.
- I fetched Chromium main's `speech_recognition.cc` and confirmed three plan claims: `install()` resolves false without `processLocally`, phrases on the cloud path raise `phrases-not-supported`, and `language-not-supported` returns before the run starts. PROVEN_PROXY.
- NOT_RUN: a real Chrome (the on-device pack exists only in the owner's own Chrome), so the 20-sentence measurement stays READY_FOR_OWNER. `quality-gate.ps1 -Fast` and the PS suites were not run; no API or script file changed.

**Mutations (mine, all different from the worker's; restored from backup, sha256 identical, tree clean)**

| Mutation | Result |
|---|---|
| Unknown setting value parses to `acik` | RED, 6 failed |
| `packQuestion === null` guard removed from `answerPackQuestion` | RED, 2 |
| `kapali` sent down the `acik` path | RED, 2 |
| `olc` flip reads `wantDevice` instead of `runDevice` | RED, 1 |
| Device-leg errors treated as fallback on today's path too | RED, 3 |
| Phrase cap removed | RED, 1 |
| `if (!this.running)` removed before `configure` | **GREEN** |
| Late-answer guard removed (probe / `useDevice` / install, one at a time) | **GREEN ×3** |
| `browserPhraseSources` returns no device aliases | **GREEN**, 2129/2129, tsc 0 |

**Pass 2 — findings**
1. **The real alias source is untested.** `browserPhraseSources` is the only place production reads `/v1/devices` aliases and capability phrases. Emptying it leaves everything green, so "contains the session's device aliases" is proven only for an injected fake.
2. **"The leg changes only between runs" (ADR decision 5) is not pinned.** The fake's `stop()` fires `onend` synchronously, so the guard is never exercised. My scratch recogniser with Chrome's delayed `end` passes on the real code and goes RED under the mutation, so the product is right and the test is missing.
3. **`olc` does not alternate when a turn speaks nothing.** With a tool result that has no speech, four utterances gave `starts=1` and four times the same leg. The names stay honest, but "alternates per utterance" holds only when a reply is spoken; neither the ADR nor the report says so.
4. **The three late-answer guards have no test**, though the code comment promises them. A scratch run (install answers after stop and a new `kapali` session) wrote nothing, so behaviour is right. Low severity.
5. **For the lead, not a defect:** `kapali` is not byte for byte. It makes one read-only `available()` call per start (plan D1). I confirmed it writes nothing on the recogniser, fetches no phrase sources and does not delay the start. Record the decision in the ADR at merge.
6. **Disclosed risk, slightly wider than written:** the probe runs before the recogniser's first `start()`. On a first-ever session, before the microphone permission exists, an installed pack reads `downloadable` and is named `chrome-bulut`. On MAIL the pack is absent today.
7. No secrets or paths in code. Aliases stay out of the log, snapshot and wire. Nothing is installed without a click; a spoken "evet" installs nothing.

RETURN (1: add a test that drives `browserLocalModeDeps(...).phraseSources` over a stubbed fetch and RED-proves that device aliases and capability phrases reach the list, including one source failing alone; 2: add a recogniser fake whose `end` arrives after `stop()` and pin that nothing is written on a started run and a late final keeps the old leg's name — RED when `if (!this.running)` is removed; 3: make `olc` alternate after a silent turn, or state the limit in the ADR's known limits and pin it with a test; 4: add tests for the late probe/install answer after stop and after a restart, or remove the comment's claim)
