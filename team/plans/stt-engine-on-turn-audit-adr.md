# ADR (number at merge): the recogniser's name of a local-mode turn is kept on the server

Task `stt-engine-on-turn-audit`, cycle d20261003. Closes ADR-0249's "Not done here - for the LEAD
at merge" line (plan D4): the web client already posts `payload.stt_engine` on every local-mode
`utterance` event; until now the server accepted the key and dropped it. It must land before the
owner's `olc` measurement can be read.

## Decision

1. **The vocabulary is the client's, letter for letter.** `app/voice/stt_engine.py`:
   `STT_ENGINES = {"chrome-cihaz-ici", "chrome-bulut", "bilinmiyor"}`, the members of
   `export type SttEngine` in `apps/web/app/lib/voice/localMode.ts` (ADR-0249 decision 3 says what
   each promises). The lead's brief suggested `web_speech_*` / `realtime`; the released client, ADR-0249
   and the open card `misheard-relay-wiring` (same payload key, into `misheard_utterances.engine`)
   all use the Turkish names, so a second spelling would split one fact into two columns. A unit
   test reads the TypeScript type and asserts the two sets are equal.
2. **Null for everything else.** `normalise(value)` returns the value only when it is exactly one of
   the three; another word, a different case, a value longer than `MAX_LEN = 64`, a non-string, a
   list, null or a missing key/payload are all `None` - never truncated into a name, never guessed,
   never raised. A paid (realtime) session sends no name today (`controller.ts` posts
   `{kind, turn, text}`), so its turns carry null; the session row's `provider` already says which
   paid recogniser it was. No `realtime` name is invented.
3. **Where it is kept** (`record_client_events`, the `utterance` branch only):
   - the `voice_intent_resolved` audit row: `metadata_json.stt_engine` (broker `audit_events`,
     JSON on SQLite / JSONB on PostgreSQL);
   - the turn record `context_json.last_utterance.stt_engine` (built fresh each utterance, so the
     latest sentence's name wins; a field not copied there is seen by no tool and no later reader -
     `misheard-relay-wiring` reads it from here);
   - `GET /v1/voice/realtime/sessions/{id}/activity`: each `intents` entry gains `stt_engine`.
4. **It decides nothing.** Route, band, tool result and the response of a turn are what they were.
   The response's `state.last_utterance` mirrors the turn record, so `session_state` shows the turn
   record WITHOUT `stt_engine` (`_without_stt_engine`); the events response is byte-equal with and
   without the key (test masks only the session id and the clock). The name is one of three fixed
   words - never the sentence (KVKK); no log line is added.
5. **No migration, no setting, no dependency, no compose/env change.** The value lives inside
   existing JSON columns; alembic head is unchanged (an integration test asserts a single head, the
   database at it, and no revision naming the key). Release is automatic under ADR-0214 addendum 9.

## The comparison (read-only, for the lead after the owner's `olc` session)

```sql
SELECT metadata_json ->> 'stt_engine'                  AS stt_engine,
       metadata_json -> 'understanding' ->> 'band'     AS band,
       metadata_json ->> 'intent'                      AS intent,
       count(*)                                        AS turns
  FROM audit_events
 WHERE action = 'voice_intent_resolved'
   AND category = 'voice_realtime'          -- service.AUDIT_CATEGORY
   AND created_at >= :since
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;
```

Names and numbers only; no sentence leaves the table. `NULL` in the first column = a turn whose
client named no engine (a paid session, an older client, or a value outside the three).

## Known limits

- No real Chrome has sent `chrome-cihaz-ici` yet (ADR-0249 known limits); proven with the relay only.
- The integration test ran on a scratch database (`pagentos_stt_engine`) on the dev stack's
  PostgreSQL, because the shared `pagentos` database was already at another branch's `0065`.
