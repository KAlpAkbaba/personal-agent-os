# ADR (unnumbered): the misheard notebook's store (misheard-ledger-store)

Status: proposed by worker `misheard-ledger-store`; the lead numbers it at merge.
Proposal: `team/proposals/2026-10-02-yanlis-anlasilan-cumle-defteri.md` (the storage half).
**Sahip incelemesi bekliyor:** the `is_request` narrowing below (TEAM_PROTOCOL 3a item 3).

## Context

ADR-0224's target is 95 % on the sentences the recogniser WROTE; the measurement stands at
68.9 % on a corpus that holds three sentences the owner really said. A sentence the system
did not understand is kept nowhere in a paid session and for one turn in a local one. This
card builds where such a sentence is kept. Nothing writes to it yet: the relay is wired by
`misheard-relay-wiring`, and no intent, tool or page is added here.

## Decision

**One table, `misheard_utterances` (migration `0065_misheard_utterances`, on
`0064_memory_vocabulary_class`).** Text only, never audio.

| column | type | note |
| --- | --- | --- |
| id | uuid, pk | |
| heard_at | timestamptz | UTC |
| sentence | varchar(2000) | the recogniser's written words |
| mode | varchar(8) | `paid` / `local` |
| engine | varchar(64), null | the recogniser's name when the client sent one |
| device_id | uuid, null | the session's device |
| band | varchar(8), null | `high` / `medium` / `low` |
| confidence | double precision, null | |
| reason | varchar(16) | exactly one of the four below |
| resolved_intent | varchar(64), null | what the router made of it |
| tool | varchar(64), null | the failed tool's name, for `tool_failed` only |
| session_id | uuid | |
| meant | varchar(2000), null | the owner's answer |
| answered_at | timestamptz, null | |
| expires_at | timestamptz | `heard_at` + 30 days; indexed |

Unique on `(session_id, heard_at)`. No foreign keys: a row outlives the session it was heard
in and leaves by its own doors only. No CHECK on `reason` / `mode` / `band`: the one writer
refuses what the CONTRACT does not name, and a CHECK is what refused the seventh memory class
in production (migration 0064 exists to widen one).

**The four reasons:** `no_intent` (the router found none), `asked_question` (layer 3 asked its
one question), `objected` (the owner said "hayır / dur" right after an action), `tool_failed`
(a tool answered that it could not).

**`tool` is 64 wide and `record()` cuts it** (the first inspection's finding 2). The CONTRACT
gave it no width; 64 is `resolved_intent`'s, and a tool's name is the same kind of name. The
other three misheard-* cards must carry the same width - the lead's to propagate.

**`record()` is the one writer.** It writes nothing and answers `None` for `listen_only`, an
empty sentence, a missing session, an unknown reason or mode. It cuts sentence / engine /
resolved_intent / tool to their widths. An unknown `band` or a confidence that is no finite
number is stored as null and the sentence is still kept (the sentence is what the notebook
is for). It is idempotent on `(session_id, heard_at)`: the second call returns the first row
and the first reason stays. Its database work runs in a SAVEPOINT, so a fault never reaches
the caller and never aborts the caller's transaction; it never commits. Under a true race
the unique constraint refuses the loser, which answers `None` - the row exists either way.

**Nothing logs the sentence, and nothing logs an exception's text.** A database error's
message carries the statement's parameters, which here are the sentence; a log line names
the event, the reason, the mode and the error's TYPE. The audit row and the route telemetry
stay wordless, as they are.

**The hold is in process.** An objection and a failed tool arrive AFTER the sentence they
are about, and the only records of that sentence (the audit row, the route telemetry) are
wordless on purpose. `hold` / `held` keep the latest sentence per session in a dict in this
process for `HOLD_TTL_SECONDS` = 120: never on disk, in the database, in the session's
`context_json` or in a log; a restart or the other colour of a release simply has nothing
(the row is then not written - a lost notebook line, never a wrong one). At exactly 120 s it
is gone. An expired entry is dropped at the next `hold` / `held` of ANY session and at each
purge pass, so an abandoned session's sentence does not stay in memory until the process
ends. `HeldSentence`'s repr leaves the sentence out.

**`is_request` - the narrowing (sahip incelemesi bekliyor).** The proposal's first condition
is "the router found no intent". Taken literally, every sentence of plain conversation in a
paid session would be stored. The lead narrowed it to the most restrictive safe option: a
sentence with no intent is a notebook row only when the understanding policy saw at least
one candidate reading, or the sentence named a machine. Plain conversation is never stored.
The owner may widen it; it is one function. It takes the count of readings or the readings
themselves, so the relay card cannot disagree with it about the type.

**Three purge triggers, none of them a session (TEAM_PROTOCOL 9).** A 30-day promise kept by
a conversation's wake-up is not kept. (1) `record()` purges on every write. (2) The owner's
GET purges before it lists. (3) The application's lifespan purges when the process starts
(every release and every restart). And `list_items` never returns an expired row even when
none of the three has run.

**The 24-hour tick is built and NOT started - ALAN_ISTEGI.** `service.PurgeLoop` is the
card's loop (a pass at start, one every 24 h, cancelled cleanly; tested in
`test_misheard_store.py`). The lifespan runs its one pass and does not call `start()`,
because a loop the lifespan starts must answer in `/v1/system/health`
(`tests/unit/test_bounded_delivery.py::test_every_background_loop_the_app_starts_can_be_seen_in_health`
reads the lifespan's source and holds a hand-written map of loop -> health key; it went RED
on the first full run with the loop started), and the map and the health endpoint's key
list (`tests/unit/test_health_endpoint.py`) are outside this card's area. The rule is right:
a loop nothing can see can die quietly. To finish it, with those two files in the area:
`PurgeLoop.health_check()`, `checks["misheard_purge"]` in `system_health`,
`await misheard_purge.start()` / `.stop()` in the lifespan instead of the one pass, and the
key in both tests. Until then a process that runs for more than 30 days with no write and no
GET keeps expired rows on disk (never listed) until its next restart.

**The owner's API** (owner session; a refusal is `{detail: {code, message}}`, Turkish):
`GET /v1/voice/misheard` -> `{items, open, retention_days: 30}`, newest first;
`POST /v1/voice/misheard/{id}/meaning` `{meant: 1..2000}` -> the item (422 `meant_empty` /
`meant_too_long`, 404 `not_found`); `DELETE /v1/voice/misheard/{id}` -> `{deleted: 1}`;
`DELETE /v1/voice/misheard` -> `{deleted: n}` ("defteri unut").

## KVKK

Text only; no audio is stored anywhere. 30 days, then deleted. The rows live in the owner's
own PostgreSQL (Hetzner NBG1) and go to no third party. There is no voice identity
(ADR-0171: confirmation instead of a voiceprint), so **another person's sentence can land
here** when it is said in the room and not understood. What stands against that: the short
retention, "unut" (one row or all), the list being the owner's alone, nothing written in
"sadece dinle", and the `is_request` narrowing.

## Release

**This change carries a migration**, so it is the exception to the automatic release rule:
its release is asked of the owner (ADR-0214 addendum 9). The migration is expand-only (one
new table, one index) and its downgrade drops the table; the old colour of a blue-green
release never writes to it, and a `record()` against a schema without the table answers
`None` and leaves the turn's transaction usable (proven on PostgreSQL).

## Evidence

`tests/unit/test_misheard_store.py`, `tests/unit/test_misheard_routes.py` (the real
application object, and its lifespan), `tests/integration/test_misheard_postgres.py` (the
dev stack's PostgreSQL after `alembic upgrade head`: columns and widths from
`information_schema`, the round trip with an over-long `tool`, the savepoint, the
downgrade). Counts and the mutation proofs are in the worker's report.

## Not done here

No sentence reaches the table until `misheard-relay-wiring`; the "defteri unut" intent, the
collector and the Onay Merkezi list are the other misheard-* cards.
