**Inspector report: understanding-threshold-policy (head `26437f4e`)**

**Pass 1: ran it**
- Policy and relay tests: 87 passed in my own run, matching the worker's number.
- Owner Utterance Suite (`test_owner_utterance_corpus.py`): 2750 passed, 0 failed (15 min). It ran on the committed tree, before any mutation. The worker's 2787 also counted the extra corpus regressions, which I did not re-run.
- Neighbouring suites (`operator_app_open_named_device`, `operator_open_application_fallback`, `voice_realtime_sessions`): 79 passed, 1 failed. The failure is `test_relay_unbound_machine_word_asks_and_dispatches_nothing`, which pins the ADR-0233 stopgap this card replaces. It is red by design, as the worker said, and the lead has to rewrite it.
- ruff check is clean on every touched path. `ruff format --check` flags only `tools_native.py`, which is not in this diff.
- Diff scope: only the 8 files in the area; nothing in `protocol_files.py`, the falsification list or the gate files.
- Thresholds file: `thresholds.json` sits beside `policy.py` and is read with `Path(__file__).with_name`. The loader refuses a missing key, a non-number or crossed bands, and the tests cover that.
- PostgreSQL: the diff adds no table, migration, store, broker or scheduler change. The only stored state is JSON in `context_json` (`understanding_pending`, `last_utterance`), so there is nothing to run on the dev stack. The JSON-column in-place-mutation risk does not apply: `ctx.pop` and the key assignments sit on a dict the relay already persists with `last_utterance`.

**Mutations (mine, not the worker's), each restored from a backup copy with sha256 equal before and after**
1. I removed the bare-"bilgisayarda" fold in `tools_operator.py`. It went RED: `test_a_bare_computer_word_is_this_machine_and_asks_nothing[rules_and_layer_one]` failed.
2. I removed the alias-word requirement in `bind_device` (carried item i). It went RED: 2 tests failed. Only the policy unit tests catch it; no relay test does.
- `git status` was clean afterwards.

**Pass 2: adversarial reading**
- The model cannot name a device. The refusal sits in `handle_tool_call` before any handler runs, and no registered schema has a device slot.
- A LOW decision runs nothing: the question rides the tool result once, and a new utterance replaces `last_utterance` and lifts the gate.
- Audit and KVKK: the `understanding` block in the audit row holds layer, band, confidence and intent names only, with no `evidence`. The `question` string lives only in the turn record, and it holds enrolled alias names, not the owner's words.
- The `Decision.acts` flag is never read by the relay. It still behaves, because a rule-less reading is not dispatched by construction. A later change could silently depend on it, though, so I'd call it dead state.
- A rule-less sentence gets a semantic score written into `ResolvedIntent.confidence`, because the code only skips this when the route was the model. For `Bugün nasılsın` that gives 0.60 on `weather_query`. I found no other reader of `.confidence`, so no behaviour changes today.

**Findings, none blocking**
- Production never builds a layer-2 engine, because no start-up code calls `configure_default_engine`. Layer 2 and the semantic/negation paths are PROVEN_AUTOMATED with `DeterministicEmbedder` only. They are NOT_RUN on `LocalEmbedder`.
- The 60-second LOW gate blocks every tool call of that turn, including an unrelated one. The worker already named this risk.
- "bulut" is not a canonical alias in `devices/aliases.py`, so a cloud-device sentence ends as the question.

**Evidence classes**
- PROVEN_AUTOMATED: the three trial sentences through the real relay with and without `DeterministicEmbedder`; the LOW single question with no dispatch; the audit block; the thresholds file; the device-slot prohibition; the MEDIUM read-back; negation capped under HIGH; the corpus suite.
- NOT_RUN: `LocalEmbedder` measurement and the owner's three sentences on MAIL (PROVEN_REAL needs the release).

**For the lead at merge**
1. Rewrite `test_operator_app_open_named_device.py::test_relay_unbound_machine_word_asks_and_dispatches_nothing` to assert the office launch and read-back, using the assertions in `test_understanding_relay.py`.
2. Wire `combine.configure_default_engine(LocalEmbedder, exemplars)` at start-up from a shipped exemplar source.
3. Update HANDOFF, the ADR number and DECISIONS.

APPROVE
