**Ürün: Anlatının ses yolu (`narrative-voice`)** — branch `team/pilot-02/worker-narrative-voice`, sha `04ab501635ee755904de64d02a9c721bda476562`, pushed, worktree clean.

**Şu an üzerinde çalışılan:** narrative-voice, alan `services/api/app/narrative` (üç yeni dosya), makine: bu PC. HANDOFF'a dokunmadım.

**Files (6, all in the area):** `model_narrator.py`, `intent.py`, `device_writer.py`, `test_narrative_voice.py`, `test_narrative_order.py`, `team/plans/narrative-voice-adr.md`.

**Evidence**
- RED first: `test_narrative_voice.py` failed at collection (`No module named app.narrative.device_writer`). PROVEN_AUTOMATED.
- GREEN: 43 tests in the voice and order files; with the existing auditor and collector tests, 78 pass. `ruff check` and `ruff format` are clean. `app.__file__` is the worktree's.
- The order test was already green because the collector sorts correctly. It is proven only by the mutation below.
- Model narrator with a fake `ChatProvider`:
  - (a) obeys: spoken exactly as written, source `model`.
  - (b) invents "47": replaced by the rule text, `draft_verdict.foreign_numbers == ("47",)`, none of its words appear.
  - (c) drops a failure: repaired (`repaired=True`, the failure is back).
  - Also covered: a skipped subsystem, provider not ok, provider raising, and an empty period (the model is never called).
  - `tell()` speaks both a good and a lying narrator correctly.
- Recogniser hits: `bu hafta ne oldu`, `dün ne oldu`, `bugün ne yaptın`, `ofiste ne yaptın`, `evde ne oldu`, `ne başarısız oldu`, plus combined forms.
- Recogniser near misses: `hafta sonu ne yapalım`, `bu hafta sonu ne oldu`, `geçen hafta ofiste ne oldu`, `dün ne yaptım` (the owner's own doing), a bare `ne oldu`, `yarın ne olacak`, `ofisi ara`, and a leading "işte" (not the work machine).

**Mutations** (each restored from a backup copy; before and after sha256 identical, full 64-hex)
1. Sort key sign flipped in `collector.py` (`849ea441f8d743b995aae0e887ca40da8d27fdce64e5977704749befcc9cbe2d`): `test_failures_are_listed_newest_first` RED.
2. Untrusted marker removed from `build_prompt` (`bf2e4df10124e8b089f0cfebf3d2da88ba648dc8428a7d675e2d23d894ce1e63`): `test_the_facts_reach_the_model_only_inside_the_untrusted_block` RED.
3. Fallback removed (rejected draft spoken as the model's own): the invented-number test RED. Same file, same sha256 as 2.
4. Other-window guard removed from `intent.py` (`81dd3e87694787a95a9d83424ebd048bc3c81e0c2d6bb891c18c12d52093ce7d`): first attempt survived. I added the near misses `geçen hafta ofiste ne oldu`, `geçen hafta ne başarısız oldu` and `bu hafta sonu evde ne oldu`, and the mutant then went RED (3 failures).
5. Overwrite guard removed in `device_writer.py` (`d78b2e744bc6a0880508052ea8e44d14edc139541bbe261996264d578ca6644b`): RED.

**For the lead at merge**
- **Voice call site (`app/voice/intents`):** call `recognise(text)`. On a hit, run `tell(db, ask.period, ask.device, ModelNarrator(chat_provider))`. Speak the result through the normal narration and TTS path. `ModelNarrator.narrate()` returns `source` and `verdict` for the turn record.
- **Explain call site (`app/explain/engine.py`):** add the "narrative" query type using the same `recognise` + `tell`.
- **Ledger writers** should wrap their `detail_json` with `stamp_device(detail, device_word_or_alias)`. Candidates (each needs checking that it holds a device):
  - `actions/receipt.py::record_receipt`
  - `operator/service.py`
  - `operator/mission_service.py`
  - `research/browser_activities.py`
  - `ambient/ingest.py`
  - `location/service.py`
  - `presence/eye.py`
  - `voice/realtime_sessions/tools*.py` (device commands)
- **ADR:** `team/plans/narrative-voice-adr.md`, unnumbered; you number it and move it into `docs/DECISIONS.md`.

**Open risks**
- `ChatProvider`'s system prompt says "you cannot see the system's records". My prompt overrides that for the turn, but a `system=` override on `assistant_chat` would be cleaner. That file is outside my area. NOT_RUN against the real Haiku; the tests use a fake provider only.
- `failures_only` is carried on `NarrativeAsk` but not applied. The narrative still lists every fact.
- `MAX_TOKENS` is 600 and the chat persona asks for four sentences. A week with many failures may get truncated. The auditor then repairs or falls back, so the failures are never lost, but the rule text may be spoken more often than expected.
- Default period is `bugün` (`bu hafta` for failures), my choice. It is reversible and recorded in the ADR.
- Evidence class: PROVEN_AUTOMATED. It becomes READY_FOR_OWNER once the lead wires the intent.
