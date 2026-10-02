## ADR (lead numbers it; an ADR-0224 addendum) — Layer 2 in production: shipped exemplars and the start-up that configures the semantic engine

**Status.** Accepted (worker, cycle d20261001, task `understanding-engine-startup`). Additive:
nothing calls it until the lead adds the one line in `app/main.py` (below).

**Context.** `combine.configure_default_engine(embedder, exemplars)` was called by tests only: the
exemplars come from `tests/voice_corpus` and production cannot import `tests/`. Production
therefore ran the rule tables, layer 1 and the policy over the RULE candidate alone.

**Decision.**
- **The exemplars ship as package data.** `scripts/export_understanding_exemplars.py` writes
  `app/voice/understanding/exemplars.json` from `exemplars_from_cases(all_cases())` - the SAME call
  the layer-2/3 tests build their engine from (all corpus sources, one entry per distinct folded
  sentence; 1427 today), so production indexes what the tests measured. `{"version": 1,
  "exemplars": [{"intent", "sentence"}]}`, sorted by (intent, sentence), one entry per line, UTF-8
  unescaped, LF, no case id / context / tool. `--check` exits 1 on drift.
  `tests/unit/test_understanding_startup.py` holds the committed file BYTE-equal to today's export
  and names the command; a corpus change without a re-export is RED.
- **The file lives beside its one reader** (`exemplars.py`), like `stt-confusions.json` and
  `thresholds.json` - not a protocol file: no second component reads it; `COPY app ./app` ships it.
- **The loader refuses a file that cannot be right**: wrong version, empty list, an entry with a
  missing or EXTRA key, an empty sentence, an intent that is not a value of the router's `Intent`
  enum. Refused whole (ValueError) - a typo never becomes an intent the policy can rank.
- **`startup.configure_understanding(settings, embedder, *, report)`** configures the default
  engine only when the memory runtime's embedder is the LOCAL semantic one (`report.active ==
  "local"` and `report.semantic`, and the object is not a `DeterministicEmbedder`). Otherwise it
  configures NOTHING and logs one line, `understanding_engine_not_configured`, with the reason:
  - the deterministic fallback is a lexical hash (the provider's own `fallback_reason` is carried);
  - a semantic provider that is not on this host (`openai`) is refused too: ADR-0224 says "no
    network call", the build would bill 1433 embeddings and every sentence would wait on HTTP;
  - `settings.understanding_semantic_enabled` false (read with `getattr`, default True) is an
    off-switch. `config.py` is outside this task's area; it becomes real when the lead declares the
    field (optional, below).
  The `report` is a required keyword because the provider report is the ONE place that says
  "semantic" (B37); deriving it again here would be a second opinion.
- **The index is built off the start-up path**, always, on a daemon thread; the function returns
  before the first embedding. Until the build is done `default_engine()` raises, which
  `policy.configured_engine()` already reads as "rule tables and layer 1 decide" - no new state in
  the policy. A build that fails logs `understanding_engine_not_configured` (reason: the exception
  type) and leaves it so. Success logs `understanding_engine_configured` (exemplars, build_ms).
  Measured on the home PC with the real `LocalEmbedder` (potion-multilingual-128M), two sessions
  (2026-10-01 and 2026-10-02, the second beside a running corpus suite): build 299-427 ms for 1427
  exemplars, return 0.5 ms, 7-8 ms per sentence afterwards. That is under the card's two
  seconds, but the Cloud Core's CPU is not this one and a thread costs nothing, so it is not
  conditional.
- **No new field on `/v1/system/health`.** The `memory` check already publishes the embedder's
  `provider`, `semantic` and `fallback_reason`, and the engine is configured exactly when that says
  `local` / `true` (and the switch is on): a second field would be the same fact with a second
  clock. What health cannot say - "the build finished" - is a start-up event, and it is in the log
  (`understanding_engine_configured` / `_not_configured`) and on every audit row
  (`understanding.layer`).

**For the lead at merge** (`services/api/app/main.py`, in `create_app`, directly after
`memory = MemoryRuntime(settings)`, today line 218):

    configure_understanding(settings, memory.embedder, report=memory.embedder_report)

with `from app.voice.understanding.startup import configure_understanding` among the imports.
Optional, for the off-switch: `understanding_semantic_enabled: bool = True` in `app/config.py`.

**Consequences / what the measurement showed.**
- The guard's two example sentences are NOT fixed by the real embedder: with `LocalEmbedder`
  "Bugün nasılsın" reads weather_query 0.62 (MEDIUM) and "Araştırmayı iptal etme" research_cancel
  0.93, capped to 0.84 by the negation rule. Neither is acted on - a reading no rule matched is
  recorded only (addendum 3) - and that rule must stay until the STT corpus says otherwise.
- With the engine live, a REPAIRED rule route (0.9) can now be contested by a HIGH semantic
  reading and become one question. Over the 2754 corpus sentences, treating every route as
  repaired (the upper bound), exactly one would: "Araştırmayı tekrar dene." (repeat 0.9 vs
  exec_retry 0.92). The Owner Utterance Suite does not see this: its apps run the deterministic
  provider, so the engine is not configured there.
- The build embeds 1433 texts through the memory runtime's embedder from a second thread;
  `LocalEmbedder`'s LRU (512) is not locked. The same object is already shared by request threads;
  a lock belongs in `app/memory/providers.py` (outside this area).
- A corpus change now needs the export re-run in the same commit (the test says so).
