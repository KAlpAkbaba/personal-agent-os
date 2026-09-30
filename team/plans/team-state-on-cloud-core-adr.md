## The team's queue, lock and reports live in the Cloud Core's PostgreSQL (pilot-02) (2026-09-30)

**Status:** accepted (worker text; the lead numbers it and moves it into docs/DECISIONS.md).
Extends ADR-0214 (the team) and ADR-0217 (the Onay Merkezi).

**Context.** `team/queue.json` and `team/lock.json` lived on one PC. With that PC off no idea or
release could be approved, and the office PC saw its own copy.

**Decision.**
- One table `team_state` (migration 0063, expand-only), keyed `(kind, key)`: a `task` row per task
  (the task as `team/queue.schema.json` states it), ONE `lock` row (machine, cycle_id, pid,
  acquired_at), a `report` row per cycle report (its text). `updated_at` is the string the writer
  stamped and is the write precondition.
- `app.team.store`: `TeamStore` with two implementations - `DbStore` (the table) and `FileStore`
  (the files, as before). `app.state.team_store` is the wiring; unset means `FileStore` over
  `app.state.team_root`, so the home PC without the API keeps working. The Onay Merkezi reads
  and writes whichever it is.
- `/v1/team/queue` (owner session): GET the queue; PUT `/tasks/{id}` with `expected_updated_at`
  (null creates; a stale or missing-precondition write is 409; a task that breaks the schema
  is 422); GET/POST `/lock` (acquire | release; free / stale (6 h) / ours / held; `takeover_dead`
  is honoured only for the caller's own machine); POST `/reports` (name.md + text).
- The schema is validated on write by a small validator over `app/team/queue.schema.json`, a copy
  of `team/queue.schema.json` that a test compares byte for byte (the image may not carry `team/`).
- `cycle.ps1 -QueueUrl <base> -QueueToken <path of a file holding the owner token>`: the queue is
  read once, only tasks that CHANGED are written back, each with the `updated_at` it was read at;
  a stale write throws and the cycle stops rather than overwrite the owner's decision. The lock is
  taken and released through the API; the report stays a file and is also POSTed as text. Without
  the parameters everything is as before.

**Consequences.** Both machines share one queue and one lock; approval works with the home PC off.
Two clocks: the six-hour staleness is judged by the SERVER's clock in API mode (the file mode uses
the machine's). Order of tasks from the database is `(created_at, id)`, not file order. The
lock's pid liveness is still the client's to check (`takeover_dead`); the server cannot see a PC's
process table. The Onay Merkezi's proposal text still comes from `team/` on the machine serving
the API. A stale write inside the same second as the last write is indistinguishable by
`updated_at`; the cycle stamps every change, so this only matters for two writers in one second.
