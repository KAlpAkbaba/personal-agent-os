## ADR (lead numbers it) — Layer 3 of ADR-0224 as built: the threshold policy, wired into the relay

**Status.** Accepted (worker, cycle adr0224-02). `resolve_intent` itself is untouched; the policy
runs in the relay (`record_client_events`), on the result the rule tables and the B51 router give.

**Decision.**
- `app/voice/understanding/policy.py` + `thresholds.json` beside it (package data, one reader, read
  with `Path(__file__).with_name`; `COPY app ./app` ships it): `high 0.85`, `medium 0.60`,
  `device_min 0.65`. A file with a missing key, a non-number or crossed bands is refused.
- `decide(candidates, *, device, device_missing, device_aliases, negated) -> Decision(band,
  candidate, question, confidence, layer, ranked, device, acts, missing)`.
  HIGH: act, receipt unchanged. MEDIUM: act and read back. LOW: one question, no action.
- **The rule candidate is the only one acted on.** A reading no rule matched is recorded (band,
  confidence, intent names) and never dispatched: the relay has no slots for it, and the lexical
  embedder scores "Bugün nasılsın" weather_query 0.60. Promotion needs slot extraction and the STT
  corpus numbers (task `understanding-stt-corpus`). The model path for such a sentence is as today.
- A repaired rule route (0.9) outranked by an acting semantic reading at HIGH is LOW: one question
  naming the two families ("Hangisi efendim: medya mı, araştırma mı?"), never a guess. An exact
  rule (1.0) is never contested.
- **Device slot** (`DEVICE_SLOT_INTENTS = {app_open}`, closed on purpose: "ofis bilgisayarları
  hakkında araştır" matches the alias at 1.0 and names no machine). Sources, in order: the owner's
  own closed form (rule parser, 1.0); the owner's answer to the question (1.0, layer `answer`);
  layer 1 (`ofisü`->`ofis` through the confusion list 0.75; a suffixed alias before the computer
  word, "ofisi bilgisayarında", 0.9); layer 2 - only ABOVE 0.65, only when an alias word is in the
  sentence (exact, or distance >= 0.75 for words of 4+ letters), never HIGH (capped at high-0.01,
  0.75 when a confusion entry was applied), and only as a canonical alias word the device port can
  bind. Anything else: `device_missing` -> the question "Hangi bilgisayarda: ev mi, ofis mi, iş mi?"
  (particle in vowel harmony). Never the session's device by default.
- A bare, unpossessed "bilgisayarda/-dan/-a" with no picking word before it ("mutfaktaki", "diğer")
  is this machine, like "bu bilgisayarda" (`names_unbound_machine`).
- A negative imperative of a known verb caps a rule-less reading just under HIGH.
- **Relay.** `ResolvedIntent` gains `band`, `candidates`; `confidence` becomes the decision's (the
  B51 model route keeps its own). The turn record gains `confidence`, `band`, `candidates`,
  `understanding {layer, band, confidence, candidates[:3], question}`; `device_targets` carries the
  alias the layers bound. The audit row `voice_intent_resolved` gains `understanding {layer, band,
  confidence, candidates[:3]}` - intent names and numbers only, never `Candidate.evidence` (KVKK).
- **Two refusals before any handler** (`handle_tool_call`, beside step-up and ADR-0075): (1) a call
  whose arguments carry a device-like key (`device`, `device_id`, `cihaz`, `bilgisayar`, `machine`,
  `computer`, `hostname`) is refused whole, `device_slot_forbidden`; (2) while a LOW turn with a
  question is fresh (60 s), EVERY tool call of that turn returns the question and runs nothing
  (`understanding_low`). The relay never speaks the question itself (no `say` frame): it rides the
  tool result once, in the paid and in the local mode alike.
- **The answer.** A device question leaves `understanding_pending {intent, application, at}` on the
  session; the next sentence, if it is ONLY an alias phrase ("Ofis.", "ev bilgisayarında") within
  120 s, re-issues that intent with the device bound. Any other sentence closes the question.
- **MEDIUM read-back** (`operator.app_open`, both launch paths): the receipt speech opens with
  "Ofis cihazında Hesap Makinesi açıyorum efendim." and continues with the outcome; no question
  mark, no second confirmation (owner rule 2026-09-18/19).

**Not changed, on purpose.** `operator.app_open.application` stays a free string resolved against
the allow-list on the server (unknown name -> a refusal naming the list). An `enum` would make a
model that heard "Spotify" pick the nearest allowed value - a guess. Every registered tool schema
already has `additionalProperties: false` and none declares a device slot (pinned by a test over
`default_registry()`).

**Consequences.** With no layer-2 engine configured the rule tables and layer 1 decide (this is
production until start-up calls `configure_default_engine`). Other MEDIUM tools do not read back
yet: only `operator.app_open` can be MEDIUM today (the device slot is the only layer-filled slot).
`CANONICAL_ALIASES` has no "bulut": such an alias ends as the question.

### For the lead at merge
1. `tests/unit/test_operator_app_open_named_device.py::test_relay_unbound_machine_word_asks_and_dispatches_nothing`
   (outside this task's area) pins ADR-0233's stopgap for the very sentence this card moves to
   MEDIUM and is RED on this branch by design. Replace its body with: commands went to
   `world.ids["GMKADIRAKBABA"]` only; `call["result"]["speech"]` starts with
   "Ofis cihazında Hesap Makinesi açıyorum efendim."; record `machine_named_unbound is False`,
   `device_targets == ["ofis"]`. (The same assertions live in `test_understanding_relay.py`.)
2. Start-up wiring (not in this area): `combine.configure_default_engine(LocalEmbedder, exemplars)`
   in `create_app`. Production may not import `tests/`: the exemplars need a shipped source.
   Until then `understanding.layer` is never `semantic` in production.
3. Nothing to register in `app/protocol_files.py` or the falsification list: `thresholds.json` is
   package data. A one-line "ships beside its reader" pin may join `test_office01_wiring.py`.
4. `docs/HANDOFF.md`, the ADR number, `docs/DECISIONS.md`.
