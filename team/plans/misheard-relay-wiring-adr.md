# ADR (number from the lead): the misheard notebook wired into the ONE relay

Status: accepted (card misheard-relay-wiring, cycle d20261003). Builds on the store
(`app.voice.misheard.service`, ADR-0254), ADR-0133 (misroute telemetry), ADR-0224 layer 3,
ADR-0208 (session device), ADR-0249 D4 (recogniser name), ADR-0171 (listen-only, not built).

## Decision

Every utterance and every tool call already passes through
`services/api/app/voice/realtime_sessions/service.py`; the notebook is written there and
nowhere else. Line anchors are as of commit of this card.

1. **objected** (`record_client_events`, ~L2113-2137). `route_telemetry.observe` now returns
   its `MisrouteCandidate`. When THIS turn completed one AND the acted event belongs to this
   session, the sentence held for the session is read (`misheard.held`, L2129) BEFORE this
   turn's sentence replaces it (`misheard.hold`, L2144), and it is recorded only when its
   `heard_at` equals the acted event's `at` - the ring is process-wide and pairs a reaction
   with the nearest acting turn, so a sentence in between, or another session's action, is
   never mistaken for the acted one. The row carries the acted sentence, its mode, band,
   confidence and intent; never "hayır". The objection sentence itself is never a row.
2. **asked_question** (L2139): the decision carries its one question (ADR-0224 layer 3).
3. **no_intent** (L2141-2143): the intent is NONE after routing, the correction and the
   pending-answer step, and `misheard.is_request(decision.ranked, machine_named)` is true.
   (2) is tried before (3); one sentence is one row (the store's idempotence on
   `(session_id, heard_at)` holds the rest).
4. Every sentence, in every mode, is then held in process (L2144).
5. **tool_failed** (`handle_tool_call`, L1051-1098, L1153, helper L1231): only a HANDLER that
   failed - a `VoiceError`, a crash, or a result `terminal_status_for` earns `failed` -
   while a sentence is held for the session; `tool` is the tool's name; `heard_at` is the held
   sentence's own, so two failures in one turn, or a sentence already in the notebook, are
   one row.

**Deliberately not a row:** a replayed `call_id`, the step-up refusal, the research
follow-up refusal, the layer-3 refusals (device slot, LOW question - that turn is already
`asked_question`), an unknown tool, an action's failed RECEIPT (a succeeded call by ADR-0077),
plain conversation with no candidate, and the objection sentence itself.

**Fields:** mode = `local` when the session's provider is `LOCAL_ROUTER_PROVIDER_NAME`, else
`paid`; engine = the event's `stt_engine` as `stt_engine.normalise` reads it, else null (never
guessed); device = the declared device, else the bound one (ADR-0208's order);
resolved_intent = null for NONE.

**Order and transactions:** the rows of a request are collected during the loop and written
at its end, after the relay's own `db.flush()` (L2538; L1240 for a tool), so the store's
savepoint never meets a pending failing row of the caller's (inspection finding 5c). Both
functions are synchronous (run in a worker thread); no store call happens inside a
transaction held across an await (finding 5b). Every store call is wrapped: a raise is logged
by its error type only (a DB error's text carries the sentence) and changes nothing the turn
returns (finding 2a is refused inside the store; a test drives each wrong type through the
relay).

**Listen-only:** `_listen_only(row)` (L1161) answers False; its future source is ADR-0171
step 3. When it is true nothing is recorded AND nothing is held, for all four conditions.
This is proven at the helper and the store, not against a real listen-only mode, which does
not exist yet.

## Known limit

Two utterances in ONE events request share the request's `now` and therefore one
`heard_at`: the second would be merged into the first row by the store's idempotence. The
clients post one utterance per request today.

## Unchanged (each held by a test)

The `voice_intent_resolved` audit metadata, the ledger's misroute note and `context_json`
gain no sentence (the local mode's `chat_question` is byte-for-byte as before); the route,
band, tool result and response of every turn are what they were.
