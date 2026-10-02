## ADR-XXXX — The narrative's model narrator is wired to `activity.explain` behind a setting that is OFF; the turn's ledger note says which narrator spoke (ADR-0244 B) (2026-10-02)

Task: `narrative-model-wiring` (roadmap order 2c). Follows ADR-0216 / 0221 / 0230 / 0244.

**Context.** ADR-0244 put the plumbing on main: the explain source narrates with the model
when the CALLER hands it a chat provider. Nothing in production handed one -
`tools.py::activity_explain` called `explain_to_briefing` without `chat_provider` - so the rule
narrator always answered, and `tell()` returns text alone, so nothing recorded which narrator
spoke.

**Decision.**
1. `Settings.narrative_model_enabled` (env `PAGENTOS_NARRATIVE_MODEL_ENABLED`), default
   **False**.
2. `activity_explain` reads the switch from the SESSION's settings (`ctx.live["settings"]`,
   the same place the provider comes from - ADR-0244 item 4; never `get_settings()`). Off, or
   no settings on the session: the call to `explain_to_briefing` is today's - no
   `chat_provider` argument, `narrative_chat_provider` is not called, no provider is built,
   no request is sent. On: `narrative_chat_provider(ctx.live)` is handed on, wrapped in a
   witness (`_NarratorWitness`) that passes the ask through unchanged and remembers how it
   ended.
3. The turn's `voice.explained` ledger row carries two more detail keys for a NARRATIVE
   answer (no other explain gets them - no narrator spoke): `narrator` (`model` / `rule`) and
   `narrator_reason` (`null` for `model`). Reasons: `setting_off`, `no_provider` (on, but the
   session has no usable key), `not_asked` (an empty window or "no failures" - a constant, no
   narrator phrases it), `timeout`, `provider_error`, the provider's own error class
   (`chat_unavailable`, `chat_busy`, `chat_refused`, `chat_model_retired`), `model_unavailable`,
   `audit_rejected`. `model` is recorded only when the stored account is exactly the model's
   draft, or the draft followed by the auditor's own repair (the draft, then
   `" Ayrıca başarısız: "` - `auditor.repair`'s marker). A bare prefix match is NOT enough: a
   rejected draft can be the opening of the rule text that replaced it ("2 iş başarısız
   oldu."), and that is `rule` / `audit_rejected` (inspector's finding on `f4da5aeb`, fixed
   and pinned by a test). The inference is from text because `tell()` returns text alone;
   one case stays open: a rejected draft that the rule text continues with that same
   marker, which needs a failed row whose own ledger summary contains the marker. The
   provider's text is compared in memory and never written: the detail holds two words. The
   same pair is logged as `narrative_narrator`.

**What switching it on means - why the default is off and the switch is the owner's.**
- Every "bu hafta ne oldu" / "ne başarısız oldu"-as-narrative ask makes ONE synchronous
  model call on the tool thread, inside the tool call's DB transaction. Nominal worst case
  with the shipped defaults: `assistant_chat_timeout_s` 20 s + one retry after 1.5 s on
  429/529 + 20 s = **41.5 s** before the rule text is spoken instead. A transport error or
  timeout is not retried (20 s). This bound is NOT a total deadline: httpx applies the
  20 s per phase (connect, write, read-gap, pool), so a response that trickles can run
  longer. A hard wall-clock ceiling would be `app/assistant_chat.py`'s to add.
- SUMMARIES OF THE OWNER'S LEDGER leave for the model's provider (Anthropic, the
  assistant-chat model): the failed rows' summaries, reasons and devices, per-subsystem
  counts and the device distribution (`model_narrator.facts_payload`) - no ids, no
  timestamps. Ledger summaries can carry text that came off the web; they go inside the
  untrusted block (ADR-0221).
- Cost: one Haiku-class request per ask (prompt = the facts, answer <= 600 tokens).

**Not closed here (outside this task's area).**
- A. `AnthropicChatProvider.answer` catches every `httpx.HTTPError`, a timeout among them,
  and returns `chat_unavailable`. So over the REAL provider a timeout is recorded as
  `narrator_reason=chat_unavailable`; `timeout` is recorded only for a provider that raises
  it. Pinned by a test. Telling them apart needs a timeout error class in
  `app/assistant_chat.py`.
- B. No compose line: `PAGENTOS_NARRATIVE_MODEL_ENABLED` is not passed to the api container,
  so production cannot switch it on yet. The line is a compose change the owner approves
  (as ADR-0248 addendum 1 did for the research rule), together with the decision itself.
- C. The router (ADR-0244 A, card `narrative-failures-router`): the owner's "ne başarısız
  oldu" with Turkish letters still goes to the explain `failures` family, not the narrative.
- D. Tokens / model id of the ask are not recorded (the card asked for narrator and reason).

**Consequences.** With the default, production's spoken behaviour does not change; the only
difference is the two new keys on narrative `voice.explained` rows (`rule`, `setting_off`).
A test factory replaced as `lambda db: source` keeps working while the setting is off.

**Evidence.** `tests/unit/test_narrative_model_wiring.py`, 17 cases through the real
application object's tool-call route: PROVEN_AUTOMATED (fake provider; the real
`AnthropicChatProvider` over a replaced transport). The real model: NOT_RUN. The owner's
voice with the setting on: READY_FOR_OWNER (the decision to switch it on, and line B).

**Rollback.** Leave the setting off (the default); or revert the commit - no schema, no data.
