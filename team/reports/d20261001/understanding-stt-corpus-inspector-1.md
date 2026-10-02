**Inspector report — `understanding-stt-corpus` @ `ed0096fc` (5 files, all inside the area; tree clean after my runs)**

**Pass 1 — run**
- **STT suite alone:** 130 passed, 1 xfailed in 67 s. Report: 106 cases, 73 correct (72 acted + 1 question) = 0.6887, 25 not understood, 8 confident wrong readings, 0 wrong-device. The three real sentences are 3/3; the office sentence ran on GMKADIRAKBABA at MEDIUM 0.75. This matches the worker's numbers. PROVEN_AUTOMATED.
- **Nightly order (Owner suite, then STT, one report file):** 2886 passed, 1 xfailed in 22 min, slow because another agent ran the same suite concurrently. Owner suite is 2754/2754 HEALTHY. The merged file keeps the owner keys and carries both numbers, 1.0 and 0.6887. PROVEN_AUTOMATED.
- **Judge mutation (mine):** wrong-device rule restricted to HIGH only → 3 failed, 8 passed. Restored from backup, sha256 `f2068a43…` before and after.
- **Product mutations (mine):** layer-1 device binding disabled → 1 failed (the read-back test: band became LOW, the product asked instead of acting). Adding the unbound-machine guard removal gave the same single failure. Both files restored, sha256 `af12597b…` and `88f6c723…` unchanged.
- **Collector query on real PostgreSQL:** the `-ShowQuery` SELECT ran on dev `pagentos-postgres` (schema `0063_team_state`, same as production), psql exit 0, 2 rows. The collector parsed the real psql output: 0 proposals, 1 audit turn. PROVEN_PROXY. Production: NOT_RUN — the lead runs it read-only over SSH.
- **Lint:** ruff check and format clean.
- **Not re-run by me:** the worker's "RED before the layers" run on the extracted `13948725` tree; the full `quality-gate.ps1` (task is not on the integration branch); the layer-2 engine measurement (NOT_RUN, as the worker said).

**Pass 2 — break it**
1. **Acceptance is not met.** The card says the test asserts ≥ 95 %; measured is 68.9 %, and the target test is a strict xfail. `pytest tests/unit` therefore stays green while the ADR-0224 target is missed. The worker reported this honestly and it cannot be closed inside the area. The task must not be recorded as "acceptance passed".
2. **"0 wrong-device" is observable on 11 of 106 cases only.** The other 95 run on one fake device, where a wrong machine cannot be seen. That includes the 8 confident wrong readings, such as "Hesapü makinesini aç." → `media_play` at HIGH 1.0. The report gives no such denominator.
3. **I could not make the run-level wrong-device gate go RED with a product mutation.** Both attempts degraded to the question. Only the judge unit tests and the worker's old-tree run prove that gate.
4. **Collector drops confirmed renderings.** "Already in corpus" is a substring match on the whole file text, so a production sentence identical to a derived one (or contained in any longer line or docstring) is skipped. A derived case confirmed by real STT is exactly what should be proposed, and no test covers it.
5. **Collector can destroy its input.** `-OutPath` equal to `-DumpPath` overwrites the dump and exits 0 (reproduced on a scratch copy).
6. **Empty `candidates` serialises as `{}`, not `[]`.** Seen in every proposal I generated; the test does not assert on it.
7. **KVKK.** Proposals hold raw sentences, which may include other people's speech. The default path `state/reports/` is gitignored; a custom `-OutPath` is not guarded. Note for the lead, not a blocker.
8. **Band inconsistency.** Derived `invented_suffix` cases accept HIGH, while the real office case says an invented word is never HIGH. Within the card's rule; flag only.
9. **No contract drift, secrets or new dependencies.** The harness imports private helpers from `test_operator_open_application_fallback.py`, so it is coupled to that file. The unit gate grows by about 65 s.

**For the lead at merge:** the worker's wiring list stands (STT file after the owner file, no xdist). A nightly that prints only the owner number will hide BELOW_TARGET, so `voice-routing-qualification.ps1` must print `understanding.stt_corpus`. Cut the product tasks that shrink `KNOWN_GAPS`: polite forms for all verb tables, a fused-word split, and exact rules that can be contested.

`RETURN (1: collector — match whole renderings instead of substrings, propose a real sentence that equals a derived one, add the test; 2: collector — refuse -OutPath equal to -DumpPath, emit candidates as []; 3: report — add the count of cases where a wrong device is observable (11) beside wrong_device_actions; 4: lead decision, not the worker's — the ≥95 % acceptance is unmet at 68.9 %, so the task stays open or is re-carded as "instrument delivered, target NOT MET" with the product follow-ups queued)`
