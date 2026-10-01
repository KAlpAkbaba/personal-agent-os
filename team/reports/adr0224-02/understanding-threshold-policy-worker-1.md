**Şu an üzerinde çalışılan:** understanding-threshold-policy · ADR-0224 layer 3 · `services/api/app/voice/understanding/` plus the relay (`service.py`, `tools_operator.py`, `intents.py`) · dev PC, worktree `worker-understanding-threshold-policy`.

**Result:** layer 3 is built, wired into the relay, committed and pushed, with one test outside my area red by design (below). sha `26437f4e7fc77ca0512900f3c919b22b4c7c42a7`; 8 files, all inside the area; worktree clean.

**What it does now**
- `policy.py` + `thresholds.json` (high 0.85, medium 0.60, device_min 0.65; package data beside its reader, shipped by `COPY app`). A file with a missing key, a non-number or crossed bands is refused.
- **Trial sentence 1**, "Ofisü bilgisayarında hesap makinesini açın": MEDIUM (0.75, confusion list), launched on the office PC, speech opens "Ofis cihazında Hesap Makinesi açıyorum efendim." then the outcome.
- **Trial sentence 2**, "Hesap makinesini aç": HIGH, receipt unchanged, session device.
- **Trial sentence 3**, the Turkish-reading sentence: intent none, no tool, no answer-mode change.
- **LOW:** the question rides the tool result once ("Hangi bilgisayarda: ev mi, ofis mi, iş mi?"); while that turn is fresh (60 s) every tool call gets the question and runs nothing.
- **Model may not name a device:** a tool call carrying a device-like key is refused whole. No registered schema has a device slot and all are `additionalProperties: false` (test over `default_registry()`).
- **Carried (i):** layer 2 fills the device only above 0.65 with an alias word in the sentence, never HIGH, and only as a canonical alias. "Bilgisayarda hesap makinesini aç" runs on the session device, no question.
- **Carried (ii):** the audit block and turn record hold layer, band, confidence and intent names only.
- **Carried (iii):** a negative imperative caps a rule-less reading under HIGH.
- **Added beyond the card:** the owner's answer ("Ofis.") within 120 s re-issues the asked launch on that device.

**Evidence (PROVEN_AUTOMATED)**
- RED first: policy tests failed on import; relay tests 28 failed / 2 passed before wiring. GREEN: 87 passed. Every relay test runs twice, with no layer-2 engine and with `DeterministicEmbedder`.
- Mutations: 15/15 RED, each restored from its backup copy with sha256 equal before and after. They include device-slot prohibition removed, MEDIUM read-back removed, LOW gate removed, evidence in the audit block, and negation cap removed.
- Owner Utterance Suite + corpus regressions: 2787 passed, 0 failed (14:19). This ran before I deleted one redundant line in the answer path; the 87 new tests, the mutations and the full unit suite ran after it.
- Full unit suite, corpus file excluded: 11170 passed, 1 failed, 5 skipped. ruff check and format clean.
- PROVEN_REAL: NOT_RUN (the owner's three sentences on MAIL after release).

**Red by design, outside my area**
`tests/unit/test_operator_app_open_named_device.py::test_relay_unbound_machine_word_asks_and_dispatches_nothing` pins the ADR-0233 stopgap (a question) for the sentence this card moves to MEDIUM. I did not touch it.

**For the lead at merge** (also in `team/plans/understanding-threshold-policy-adr.md`)
1. Replace that test's body with the office-launch and read-back assertions (the same ones are in `test_understanding_relay.py`).
2. Call `combine.configure_default_engine(LocalEmbedder, exemplars)` at start-up. Production cannot import `tests/`, so the exemplars need a shipped source. Until then production runs the rule tables and layer 1 only.
3. Nothing to register in `protocol_files.py` or the falsification list.
4. HANDOFF, the ADR number, DECISIONS.

**Not done / deviations**
- A reading no rule matched is recorded, never dispatched: the relay has no slots for it, and the lexical embedder scores "Bugün nasılsın" as weather_query 0.60. The model path for those sentences is as today.
- `operator.app_open.application` stays a free string resolved against the allow-list, not an enum: an enum would make the model pick the nearest allowed app for an unknown name. Your call.
- Only `operator.app_open` reads back at MEDIUM; no other tool can be MEDIUM today.
- A named device is bound for `app_open` only.

**Open risks**
- The LOW gate blocks every tool for 60 s after a question, including one the model calls for an unrelated reason.
- "bulut" is not a canonical alias in `devices/aliases.py`, so such a device ends as the question.
- Layer 2 on `LocalEmbedder` is unmeasured here.
