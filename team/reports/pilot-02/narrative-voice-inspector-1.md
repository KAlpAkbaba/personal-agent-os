**Verdict: APPROVE**

**Pass 1: run it**
- I re-ran the voice, order, auditor and collector tests from the worktree with the main venv. `app.__file__` points at the worktree. **81 passed**; the worker said 78, so my count is higher, not lower.
- `ruff check` on `app/narrative` and the narrative tests is clean.
- I made 6 mutations of my own, none of them the worker's. Each was restored from a backup copy, and the sha256 before and after were identical each time. The two hashes were:
  - `collector.py`: `849ea441f8d743b995aae0e887ca40da8d27fdce64e5977704749befcc9cbe2d`
  - `intent.py`: `81dd3e87694787a95a9d83424ebd048bc3c81e0c2d6bb891c18c12d52093ce7d`
  - `model_narrator.py`: `bf2e4df10124e8b089f0cfebf3d2da88ba648dc8428a7d675e2d23d894ce1e63`
- The mutation results:
  1. `model_narrator.py`, the marker-forgery replace disabled: **RED**, `test_a_ledger_summary_that_pretends_to_end_the_block_cannot_escape_it`.
  2. `model_narrator.py`, `repair()` skipped (`text = draft`): **RED**, the drops-a-failure test.
  3. `model_narrator.py`, the fallback gate forced (`if True:`): **RED**, 3 tests (invented number, skipped subsystem, answer without the facts).
  4. `intent.py`, `yaptın` widened to also accept `yaptım`: **RED**, the near miss `dün ne yaptım`.
  5. `intent.py`, `yaptın` widened by an optional extra `ı`: survived (40 passed). That mutant is nearly equivalent, so I don't count it against the worker.
  6. `collector.py`, the sort removed entirely: **survived** (3 passed). This is the one to note; see below.
- I did not re-run the worker's `device_writer.py` overwrite-guard mutation or their other-window guard in `intent.py`. I only checked that their reported hashes match the current files.

**Pass 2: break it**
- **Order test:** removing `_newest_first` does not turn it RED, because `ledger_service.query` already returns rows newest first (`_fetch`). The card's requirement, RED when the sort key changes, is met: the worker's sign flip goes RED. The collector sort is a second layer, not the only one. Not a defect.
- **Rejected text does not leak:** on `audit_rejected` the returned `Narration` is built by `RuleNarrator` from the facts alone. The draft appears only as `draft_verdict`, which is a verdict and not text.
- **Untrusted data:** the facts are JSON under `UNTRUSTED_BEGIN`/`END` markers, and a summary that tries to forge the end marker is neutralised (test and mutation above). Ids and timestamps are stripped from the payload. `now_tr` still puts a date into the provider call. Any digits the model repeats from it go through the auditor.
- **Contracts:** no diff to `app/voice`, `app/explain`, `vocabulary.py`, HANDOFF, DECISIONS or `BUILD_STATE.json`. No new dependency: it reuses `ChatProvider`. No secrets or absolute paths found by grep.
- **Privacy (KVKK):** ledger summaries go to the cheap model, which is the same cloud brain that already sees them. Nothing new goes into audit or logs. The exception log keeps only `str(exc)[:200]`.
- **CPU and memory:** one call per ask, `MAX_TOKENS` 600, and none when the period is empty. Nothing runs at import time.
- **Rollback:** additive files only, and nothing calls them until the lead wires it.
- **Open risks I agree with:**
  - `ChatProvider`'s system prompt says it cannot see the records, and the user prompt overrides that for the turn. A `system=` override would be cleaner.
  - `failures_only` is carried on `NarrativeAsk` but not applied.
  - A truncated 600-token answer falls back to the rule text more often than expected.
  - The default periods (`bugün`, and `bu hafta` for failures) are the worker's choice, recorded in their ADR.

**Evidence classes**
- Recogniser, order, model-narrator behaviour with a fake provider, audit, repair and fallback: PROVEN_AUTOMATED.
- Real Haiku behaviour: NOT_RUN. I did not call the real model.
- Voice path end to end: READY_FOR_OWNER once the lead wires `recognise` and `tell` into `app/voice/intents` and `app/explain/engine.py`. The worker's report already lists those two call sites, the `stamp_device` writer candidates, and the ADR in `team/plans/narrative-voice-adr.md` for the lead to number.

`APPROVE`
