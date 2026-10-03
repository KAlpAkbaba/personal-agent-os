# ADR addendum (unnumbered): the misheard purge never holds the start (misheard-purge-start-bounded)

Status: proposed by worker `misheard-purge-start-bounded`; the lead numbers it as an addendum
of the misheard store's ADR (`team/plans/misheard-ledger-store-adr.md`).
Source: `team/reports/d20261002/misheard-ledger-store-inspector-2.md`, findings 1-4.

## Context

`PurgeLoop.start()` ran its first purge pass and waited for it. With an expired row locked by
another transaction the real application did not start within 8 s, and started the moment the
lock was released: there is no `lock_timeout` anywhere. Every other loop of the lifespan
(`RetentionSweeper`, `SelfModelRefresher`) only creates its task in `start()`. Nothing can
reach this today (no writer, no row). It becomes reachable once `misheard-relay-wiring`
writes and a row is 30 days old, because `record()` purges inside the voice turn's transaction.

## Decision

1. **`start()` only creates the task.** The first pass is the task's first iteration
   (`_loop`: pass, then sleep the interval). The application serves before the first pass
   has run. `stop()` cancels the task at once, also while a pass is blocked. The thread's
   statement then finishes or fails on its own and never reaches the application.
2. **The pass is bounded.** On PostgreSQL the pass's transaction begins with
   `SET LOCAL lock_timeout = '<PURGE_LOCK_TIMEOUT_S>s'` (`PURGE_LOCK_TIMEOUT_S = 3`).
   `LOCAL`: the setting ends with the pass's transaction and never reaches a pooled
   connection (a test reads `SHOW lock_timeout` before and after). A pass that meets a locked
   row gives up after 3 s. It counts one failure in the loop's heartbeat, with the
   exception's class only (`OperationalError`), never its text. SQLite has no such setting
   and no row locks; the statement is skipped there.
3. **What a skipped pass costs.** The expired row lives until the next pass (24 h later, or
   the next `GET`/`record()` purge). It is never listed meanwhile: `list_items` and `answer`
   already filter on `expires_at` themselves. Health keeps `status: ok` with `failures` /
   `last_error` set (the house `LoopHeartbeat` semantics).
4. **The clock.** `purge_once(moment)` takes the moment the loop read on the event loop. One
   clock still decides both what has expired and whether the loop is behind.
5. **`record()` refuses a caller's type error.** A `reason` or `mode` that is not a string,
   or a `heard_at` / `now` that is neither `None` nor a `datetime`, answers `None` like every
   other refusal. Nothing is written and the caller's transaction stays usable.
6. **Two tests for mutants that survived.** The purge at the exact expiry instant
   (`expires_at <= now`; one microsecond before it, the row stays). A purge that raises
   inside `record()` is logged by its class (`trigger=record`) and the row is still written.
7. **`POST /v1/voice/misheard/{id}/meaning` reads its own body.** A body that is not JSON
   answers `422 {detail: {code: "body_invalid", message: <Turkish>}}` instead of FastAPI's
   English `{detail: [...]}`. A JSON array answers the existing `meant_empty` refusal. A lone
   surrogate in `meant` (valid JSON that no UTF-8 can carry) answers
   `422 {code: "meant_invalid"}` instead of an unhandled `UnicodeEncodeError` (500). No
   refusal echoes the body, and the stored row is unchanged. An empty body is still "no
   meaning" (`meant_empty`).

## Consequences

- `app/main.py` is unchanged: the lifespan still calls `start()` and `stop()`.
- A test that read `purge.passes == 1` right after the lifespan started now waits for the
  pass (a hang guard, not the claim).
- Carried into `misheard-relay-wiring` (not this card): inspector 2's finding 5 (a second
  writer of one key waits for the first transaction; flush before calling `record()`).
