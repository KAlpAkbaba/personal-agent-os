**Inspector report 2 — `understanding-stt-corpus` @ `1f81f8bd`** (5 files, all inside the area; tree clean after my runs, sha256 `stt_harness.py` `e1afdd4f…`, `collect-stt-corpus.ps1` `55249003…` unchanged)

The three returned defects are fixed and hold under my own runs; the card's ≥ 95 % acceptance is still unmet at 68.9 % (73/106) and only the lead can settle that.

**Pass 1 — run**
- **STT suite alone:** 135 passed, 1 xfailed in 68 s, matching the worker. PROVEN_AUTOMATED.
- **Nightly order (owner file, then STT, one report file):** 2891 passed, 1 xfailed in 21 min 57 s. Owner suite 2754/2754 HEALTHY. The merged file carries both numbers: 1.0 and 0.6887 (72 acted + 1 question, 25 not understood, 8 confident wrong readings, 0 wrong-device over 11 observable cases, `BELOW_TARGET`). PROVEN_AUTOMATED.
- **Judge mutation (mine):** the session device tolerated as a non-stranger, which is the 2026-09-30 shape → 4 judge tests RED.
- **Collector mutation (mine):** dump-path comparison made case-sensitive → `test_the_collector_never_writes_over_what_it_reads` RED. Both files restored from backup.
- **Real PostgreSQL (the worker's NOT_RUN):** the `-ShowQuery` SELECT ran on dev `pagentos-postgres` (schema `0063_team_state`), psql exit 0. I inserted 4 session rows inside a transaction, ran the SELECT, and rolled back (0 rows remain). The collector parsed the real output: 3 proposals, 1 real rendering skipped, and "Hesapü makinesini aç." came back with `confirms_derived_case = stt.derived.op.app.8.invented_suffix`. Embedded quotes and a backslash survived. PROVEN_PROXY.
- **Lint:** ruff check and format clean; the ps1 has 0 non-ASCII bytes.
- **NOT_RUN:**
  - Collector against production: the lead runs it read-only over SSH.
  - Audit rows from real Postgres through the collector: the dev database had none in the window, so that half is covered by the fixture only.
  - Full `quality-gate.ps1`: the task is not on the integration branch.
  - Layer-2 engine measurement.

**Pass 2 — break it**
1. **Acceptance is still not met.** The target test is a strict xfail, so `pytest tests/unit` stays green at 68.9 %. The ADR now says "instrument delivered, target NOT MET", but the card is unchanged. The task must not be recorded as "acceptance passed".
2. **Return points 1–3 hold.**
   - Whole-rendering match: a trimmed real rendering is skipped, and its tail or its `meant` is proposed.
   - `-OutPath` refusals: `.PY`, the dump, and `state/reports/../../docs/x.json` are all refused.
   - Dump encodings: UTF-16 and UTF-8-BOM dumps (what a PowerShell `>` redirect produces) parse identically.
   - Corpus shape: a single-quoted rendering or a `"…" + ""` derived entry stops the tool with exit 1 instead of matching less.
3. **The tracked-tree guard covers only the script's own checkout.** Run from this worktree, `-OutPath <main checkout>\docs\x.json` was written (0 proposals; I deleted it). After merge the script lives in main, so the gap is sibling worktrees only. Flag, not a blocker.
4. **The run-level wrong-device gate is still proven only by the judge unit tests.** No product mutation makes it RED, and the zero covers 11 of 106 cases. The report now says so.
5. **Hard-coded counts.** `== 11` observable cases, 3 real cases and the three-sentence list are literals in the tests, so every real rendering the collector leads to will need those edited. That is a deliberate ratchet, but it is friction for the tool's own purpose.
6. **Line endings.** The ps1 is LF in the working tree while `.gitattributes` says `eol=crlf` (sibling scripts are CRLF). Harmless: nothing hashes the file from git.
7. **Standing from report 1:** the harness imports private helpers from `test_operator_open_application_fallback.py`, and derived `invented_suffix` cases accept HIGH while the real office case never does.
8. **No contract drift, secrets, new dependencies or product code.** `chat_question` is written by `service.py:1972`, as the collector assumes. Fixture sentences are synthetic.

**For the lead at merge**
- Re-card the task as "instrument delivered, target NOT MET" and queue the product work that shrinks `KNOWN_GAPS` (polite forms for every verb table, a fused-word split, contestable exact rules). My verdict covers merging the instrument only.
- Run the STT file after the owner file, without xdist; the pair takes about 22 min here.
- Make `voice-routing-qualification.ps1` print `understanding.stt_corpus` including `wrong_device_observable_cases`; otherwise the nightly hides `BELOW_TARGET`.
- Number the ADR in `team/plans/understanding-stt-corpus-adr.md`.
- Run the collector against production over SSH before recording it as anything above PROVEN_PROXY.

`APPROVE`
