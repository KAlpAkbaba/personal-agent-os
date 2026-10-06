# ADR draft: one open conversation is a database rule (conversation-start-race-many-open)

Status: proposed (worker-4, cycle d20261006). The lead numbers it.

## Context
Test round t-manual-20261006d (tester-3, staging 72884b71): eight concurrent
`POST /v1/conversations` (phone and web starting capture at once) left four conversations
open. `service.start_conversation` checks for an open row and then inserts; every racer passed
the check before any insert committed. Reproduced locally: 8/8 opened at service level,
5 x 201 through the real application.

## Decision
- Migration `20261006_0071_conversation_one_open.py` (revision `0071_conversation_one_open`,
  chained from `0072_money_ledger`, the head of the base; file name is the card's - the lead
  re-numbers/re-points at merge): partial unique index
  `uq_conversations_one_open ON conversations ((true)) WHERE ended_at IS NULL`.
  A second open insert waits on the first and fails with a unique violation - no timing.
- Before building the index the upgrade ends every open conversation but the newest
  (`ended_at = CURRENT_TIMESTAMP`, nothing deleted): staging already holds several open rows
  and the index could not be created over them. The downgrade drops the index only.
- `routes.py`: the start route maps an `IntegrityError` whose constraint is that index to
  `ConversationRefused("already_open")` -> `409 {code: already_open}`, the answer the client
  already reads for the sequential case. Any other integrity error keeps `409 store_conflict`.
- The service's check stays (the common, sequential case never touches the index).

## Consequences
- `app/conversations/models.py` does not declare the index (outside this card's area);
  `test_migration_model_agreement` passes as is. A follow-up may add it to `__table_args__`.
- An upgrade on a database with several open conversations silently ends the older ones.
