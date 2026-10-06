# ADR draft: a conversation's next line number is taken under the conversation's row lock

Status: accepted (task conversation-segment-race-loses-lines, cycle d20261006)

Context. The test team (round t-manual-20261006d, staging 72884b71, tester-3) posted lines to
one conversation at once, as the phone and the web capturing the same talk do: of two, one came
back 409 `store_conflict`; of four, two were lost. `add_segment` read `max(seq)` and inserted
`max + 1`; the losers hit `uq_conversation_segments_seq`. The order was kept, a line of the
owner's conversation was not.

Decision. `add_segment` reads the conversation with `SELECT ... FOR UPDATE` (`db.get(...,
with_for_update=True, populate_existing=True)`) before anything else, and only then reads
`max(seq)`. Concurrent writers of ONE conversation queue on that row; under READ COMMITTED each
`max(seq)` statement after the lock sees the number the previous writer committed. `seq` stays
gapless and in arrival (lock) order. Writers of different conversations do not wait on each
other. The lock also orders a line against a concurrent stop (`ended_at`), so no line is written
into a conversation that a committed stop has closed.

Rejected. A retry of the insert on the unique violation: it needs a savepoint per attempt, a
bound, and still loses a line past the bound under load; the lock never loses one. A sequence
per conversation: a gap on every rolled-back write, and one database object per conversation.

Consequences. The routes already run each request in one transaction that commits at the end
(`routes._run`), so the lock is held for one line's work (milliseconds). SQLite (unit tests)
ignores FOR UPDATE; the unit test proves the statement order, the real race is proven on
PostgreSQL (`tests/integration/test_conversations_segment_race_postgres.py`, 16 at once through
the service and 16 through the real route). The 409 `store_conflict` branch stays for any other
unique violation.
