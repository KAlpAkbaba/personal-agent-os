# ADR draft: a new household item is inserted under a savepoint and a key conflict lands on the winner's row

Status: accepted (household-item-create-race-500, cycle d20261006)

## Context

The test team (round t-manual-20261006d, staging 72884b71) sent one NEW item name to
`POST /v1/household/items` from 2/16/32 clients at once and got HTTP 500 for 1/2, 11/16, 28/32.
`service._get_or_create` reads by folded key, sees nothing, inserts; a concurrent request that
committed the same key in between makes `uq_household_items_key` raise an unhandled
`IntegrityError`. The owner saying "X bitti" on the phone and the desktop at once hits it.

## Decision

The insert runs inside `db.begin_nested()` (a SAVEPOINT). On `IntegrityError` only the savepoint
is undone; the row holding the key is re-read by key and the request applies its own change
(level / list flag / quantity) to that row and commits as usual. If the re-read finds nothing
(the winner's row was deleted in between) the original error is raised - no silent loop.

Chosen over `INSERT ... ON CONFLICT DO NOTHING`: it is dialect-neutral (the unit suite runs on
SQLite), keeps the ORM object path unchanged, and a savepoint keeps whatever the session already
wrote earlier in the same transaction. The unique key in the database stays the one guard.

## Consequences

- Concurrent creates of one name: every request answers 200, exactly one row exists; under
  READ COMMITTED each later request sees the committed row and updates it in turn (the last
  writer's level/quantity is the row's value; each depletion event is still recorded).
- Proof: `tests/unit/test_household_create_race.py` (forced blind read on SQLite) and
  `tests/integration/test_household_create_race_postgres.py` (16 concurrent requests through
  `create_app` on real Postgres with the first 8 held together after their empty read; 16
  concurrent `add_to_list` on the service).
