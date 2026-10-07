# ADR draft: open alarms are bounded by the list page (alarm-created-shows-in-list)

**Context.** Test round t-w10070808, improvised case `t3-invisible` on staging b1f8ef94:
with 767 alarms waiting (left by the round's own load ladders), `POST /v1/alarms`
`{when: {date: 2026-10-10, time: 08:00}, label: tj3-gorunmez}` answered 201 and
`GET /v1/alarms?limit=200` did not contain it. Not a test-alarm filter, a timezone window or
a state: `list_alarms` orders open alarms by `scheduled_for` and reads at most 200 rows, and
nothing kept the number of open alarms at or under 200. The same 200-row read is what the
tick, `next_alarm` and `alarms_ringing` see, so such an alarm was also never armed - a 201
for an alarm that would not ring. The alarm was not "rightly hidden".

**Decision.**
- `app.alarms.service.MAX_OPEN_ALARMS = 200`: `create_alarm` refuses the alarm that would be
  open past it (`AlarmLimitReached`, a subclass of `InvalidAlarmRequest`). On PostgreSQL the
  count-then-insert runs under `pg_advisory_xact_lock`, held until the first commit, so
  concurrent creates at the bound cannot overshoot it.
- `POST /v1/alarms` answers it with 409 `lifecycle_violation` and a Turkish sentence ("Şu an
  200 alarm bekliyor; daha fazlasını kurarsam listede görünmez. Önce birkaçını iptal eder
  misin?"); no row, no routine is written.
- `GET /v1/alarms`: the default `limit` is the bound (200, was 100), `MAX_LIST_LIMIT` reads
  `MAX_OPEN_ALARMS`, and the answer carries `total` (open alarms), so a shorter page says so.

**Why not paging.** Paging fixes only the list; the tick and the next-alarm answer would
still stop at 200. One owner does not have 200 alarms waiting; a bound that equals what
every reader sees keeps "201 = listed and will ring" true without a cursor on each reader.

**Consequences.** The test team's load ladders that create hundreds of alarms now see 409
past 200 open alarms (expected; their cleanup cancels them). The voice tool
`alarm_create` (`app/voice/realtime_sessions/tools_ambient.py`, outside this card's area)
calls `create_alarm` without catching `AlarmLimitReached`; at the bound it fails as an
unexpected tool error instead of a Turkish refusal - follow-up card. With
`include_terminal=true` the page may still end before the newest open alarm (terminal rows
share the 200), unchanged here.

Evidence: `tests/integration/test_alarm_created_listed_pg.py` (dev Postgres, scratch DB).
