## ADR (lead numbers it) — ADR-0224 measurement as built: the STT corpus, its judge, and the first number (68.9 %, target NOT met)

**Status.** Accepted (worker, cycle d20261001). Tests and one tool only; no product code changed.

**The number (2026-10-01, main 858c3e0b, layers 1-3, no layer-2 engine as in production).**
106 cases: 3 real (the trial of 2026-09-30) + 103 derived. **73 correct = 68.9 %** (72 done at
HIGH/MEDIUM + 1 question at LOW), **0 wrong-device actions**, 25 not understood (left to the model),
8 read as ANOTHER intent at HIGH 1.0. The three real sentences: 3/3. By distortion: diacritics
24/24, invented suffix 18/21, polite 16/29, fused 12/29. **The 95 % target of ADR-0224 is not met.**

**Decision.**
- `tests/voice_corpus/stt_corpus.py`: `SttCase(rendering, meant, intent, tool, application, device,
  bands, origin, distortion, base_case_id, heard_at)`. `origin="real"` only for what production
  heard (dated); `origin="derived"` for a canonical sentence with ONE named distortion (`polite`,
  `diacritics`, `fused`, `invented_suffix`), `meant` = the canonical sentence letter for letter.
- **The bases are a rule, not a choice**: per `corpus.py` category, the first canonical single-turn
  case that names an intent, expects `ok`/`control`, has 2+ words and ends in a verb of
  `IMPERATIVES` (26 categories), plus the trial family (`op.app.8`, `op.app.office.1`,
  `op.app.home.1`). Every distortion that applies is taken. The unit test recomputes the rule and
  checks each rendering's shape, so the corpus cannot be tuned to pass.
- `tests/voice_corpus/stt_harness.py`: the same path as `harness.py` (POST /events -> router ->
  policy -> POST /tool-calls -> device). Two worlds: a derived case runs through `run_case` of the
  owner corpus with the rendering in place of the sentence; a case that names a machine, and the
  three real ones, run over two enrolled devices behind the real `BrokerDeviceAction`, the session
  bound to the machine the sentence does NOT name - `harness.py`'s single fake device cannot show
  which machine acted. The harness plays the model and calls the meant tool even when the layers
  understood nothing: that call landing on the session's machine is what 2026-09-30 was.
- `judge(case, seen)` (pure): any command on a machine other than the meant one = `wrong_device`
  in every band; meant intent+entities executed at HIGH/MEDIUM = `correct`; LOW with exactly one
  question and nothing run = `question` (correct); LOW with no question = `not_understood` even
  when the model's guess lands; another reading at HIGH/MEDIUM = `wrong_reading`
  (`confident_wrong_readings` in the report).
- **The target test is a strict expected failure, with a ratchet under it.** `KNOWN_GAPS` (33
  cases, each with its verdict) must EQUAL the failing set: a new failure names itself, a case the
  layers learn must be removed, a real case or a `wrong_device` can never be listed. Asserting 95 %
  outright would hold every release red for a gap only later work can close (the shape of "an
  alarm that blocks its own remedy"); bending the corpus to pass would repeat the lesson ADR-0224
  opens with. `0 wrong-device` and the three real sentences are hard assertions.
- Report: `write_reports` adds `understanding {owner_corpus, stt_corpus}` and the full `stt_corpus`
  run to the owner suite's report file (`PAGENTOS_VOICE_CORPUS_REPORT`) - only when that file is
  the owner suite's own, never creating it - and/or writes `PAGENTOS_STT_CORPUS_REPORT`. With no
  owner report the owner number is `NOT_RUN`, never invented.
- `scripts/voice/collect-stt-corpus.ps1`: parses a read-only JSON-lines dump into a proposals JSON
  (`origin real`, `status needs_owner_meaning`, meaning slots null); refuses a `.py` or the corpus
  path as output; `-ShowQuery` prints the one SELECT.

**Found on the way (not fixable inside this task's area).**
1. Production keeps the owner's sentence in ONE place: `last_utterance.chat_question`, local mode,
   intent none, one turn. A paid session keeps none and the audit row holds no word (KVKK). So
   "real renderings from the production audit" can only come from local-mode misses; collecting
   paid-session renderings needs a recorded, owner-approved capture - a new idea for the owner.
2. Eight distorted sentences match ANOTHER intent's rule at HIGH 1.0 ("Hesapü makinesini aç." and
   "Notü Defteri'ni aç." -> `media_play`; "Kendi kendini geliştirmeyi duraklatın." -> `explain`;
   "Şubug'ı kendin düzelt." -> `memory_correct`; "Buresmi Paint'te yeniden çiz." -> `repeat`). An
   exact rule is never contested (addendum 3), so layer 3 cannot catch them.
3. The polite imperative is read only by the open-verb tables (ADR-0233): "Ekranları kapatın.",
   "Haberleri açın.", "Maillerime bakın." resolve to nothing.

**Consequences.** The nightly run reports two numbers once the lead adds this file to it. Closing the
gap is product work (polite forms through layer 1 for every verb table, a fused-word split, a
semantic reading allowed to contest an exact rule); each fix shortens `KNOWN_GAPS`.
