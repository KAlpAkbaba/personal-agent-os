# ADR draft — watch creation: a lock-free first look, the lock's count decides (watch-concurrent-create-errors, 2026-10-07)

**Context.** Round t-w10070808 (staging b1f8ef94) reported POST /v1/watches "16 at once -> 12
errors". The raw result (`tj-t-w10070808-5.result.json`) shows the ladder never cleared between
steps: 2 watches from its steps + 2 + 4 + 8 = 16 already stood, so the 16 step was 4 x 201 and
12 x 422 "En çok 20 nöbet" - the cap working, no 5xx, no lost row (the 32-from-empty scenario
gave exactly 20 x 201 + 12 x 422). The real defect was elsewhere: the strict xfail
`watch-cap-race-21` never turned XPASS after the advisory lock (`pg_advisory_xact_lock`) went in.
With the count behind the lock, `fire_together`'s `meet_after` held each request's count for its
full 3 s wait one after another (25 x 3 s > the 60 s guard); the requests still queued for the
15-connection pool timed out into a 500.

**Decision.** `create_watch` keeps the lock and its count as the only thing that admits a watch.
Before it the route takes `service.refuse_when_full` in a session of its own (its connection given
back before the locked turn takes one): a full cap is refused without waiting on the lock or
opening a write, and concurrent requests read the count side by side. Inside the lock the count
is taken again (READ COMMITTED: a new statement after the lock sees every earlier holder's
commit), so the first look can only refuse early, never admit.

**Consequences.** 16 at once -> 16 x 201 / 16 rows; 25 at once -> 20 rows + 5 Turkish refusals;
a full cap refuses a burst of 16 without touching the lock; each run 4-19 s on the dev Postgres
(the lock-only route: 46-73 s and timeouts). A full-cap request with a bad field now gets the cap
refusal instead of the field's (the cap is looked at first). One more `count(*)` per creation.
The strict xfail in `test_two_devices_same_time_pg.py` is XPASS(strict) and its mark must go
(outside this card's area: ALAN_ISTEGI).
