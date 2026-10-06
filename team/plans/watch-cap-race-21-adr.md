## ADR — The 20-watch cap holds under concurrency: one advisory lock around count + insert (2026-10-06)

*Worker's draft for card watch-cap-race-21 (cycle d20261006); the lead numbers it.*

**Context.** The test team (round t-manual-20261006d, staging 72884b71, tester-4) sent 32
POST /v1/watches at once from empty and got 21 watches. `create_watch` counted the rows and
then inserted; two transactions that both counted 19 both inserted. SQLite (the unit suite)
writes serially, so only PostgreSQL showed it.

**Decision.** On PostgreSQL `create_watch` takes `pg_advisory_xact_lock(CAP_LOCK_KEY)`
(`0x57415443`, "WATC") before the count. The lock lives until the caller's commit or
rollback (the route commits right after; a refusal rolls back when the session closes), so
count and insert are one turn per request. The refusal is unchanged: 422
`En çok 20 nöbet tutulabilir; önce birini kaldır.` On SQLite nothing is taken.

**Rejected.** SERIALIZABLE isolation for the one transaction (retries would have to be
written in the route; a lost retry is a 500); a counter row with `SELECT ... FOR UPDATE`
(a migration and a second source of truth for the count); a database trigger (logic out of
the service, a migration).

**Cost.** Watch creations on the Cloud Core queue behind each other; one creation is a few
milliseconds and the owner makes a handful a day.

**Evidence.** `tests/integration/test_watch_cap_race_postgres.py`: a cursor hook widens the
window after each `count(*)` to 0.2 s; 32 threads from empty -> exactly 20 rows and 12 refusals
with the Turkish sentence. With the lock call removed (mutation, restored from a backup) the
same test makes 32 rows.

**Two smaller notes from the same tester, judged without a change.**
- A long Turkish label answered 422: that is the label limit (`LABEL_WIDTH` = 80, the column
  width), and the message is already Turkish: `Nöbetin adı en çok 80 karakter olabilir.`
- `DELETE /v1/watches/abc` answers 404 `Bu nöbet yok; silinmiş olabilir.`: the route maps a
  non-uuid id to not-found on purpose (no such watch can exist); kept as the contract.
