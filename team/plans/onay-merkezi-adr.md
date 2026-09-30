## ADR (number by the lead): The Onay Merkezi writes decisions into the queue, for the next cycle

**Context.** The owner decides at two gates in the queue: `awaiting_owner` (idea) and
`awaiting_release`. Until now the owner edited `team/queue.json` by hand.

**Decision.**
- `services/api/app/team` reads `team/queue.json` as data. `GET /v1/team/approvals` lists the
  two gates with proposal/report, the newest `team/reports/*.md`, and `cycle_running`.
  `POST /v1/team/approvals/decision` takes `approve` -> `approved` (idea) / the release flag (see below), `reject` -> `stopped` with a
  required reason. Both write `state`, `updated_at` (and `reason`) and nothing else, atomically.
- A decision is refused (409 `cycle_running`) while `team/lock.json` is held and younger than
  6 h: the cycle's own read-modify-write would overwrite it. Never applied mid-run.
- The ledger event is recorded BEFORE the queue write; a ledger refusal (503 `ledger_refused`)
  leaves the queue untouched. No decision exists that the ledger does not know.
- Voice (later task) uses the same POST with `channel: "voice"`: `gate` (`fikir` / `yayin` /
  `yayın`) is required, and the task must resolve to exactly ONE task at that gate, else
  422 `gate_required` / 409 `ambiguous` / 409 `nothing_waiting`. The shell may send `gate` too;
  a mismatch (page out of date) is 409 `gate_mismatch`.
- Approving a release changes the queue file only. Nothing here starts a release.

**Lead wires at merge (outside the worker's area):**
1. `app/ledger/vocabulary.py`: subsystem `team`; event types `team.task.approved`,
   `team.task.rejected` (constants in `app/team/approvals.py`). Until then every decision is
   refused with `ledger_refused` (fail closed; tested).
2. `app/main.py`: `app.include_router(team_router)` from `app.team.routes`.
   `app.state.team_root` may override the default repo-root `team/` (the Cloud Core VM has no
   checkout; the route is meaningful where the queue lives, i.e. the dev/home Core).
3. The web shell's navigation link to `/core/approvals` (file: apps/web/app/core/approvals).

**Release approval (decided, lead's return note).** `awaiting_release` + Onayla does NOT write
`approved` (the cycle reads that as "assign a worker"). It keeps the state and writes
`release_approved=true`, `release_approved_at` (UTC `YYYY-MM-DDTHH:MM:SSZ`), `release_approved_by`
(`shell` | `voice`; `owner_sentence` is the lead's own write). The idea gate still writes
`approved`; Reddet writes `stopped` + `reason` at both. The lead must add the three fields to
`team/queue.schema.json` (`additionalProperties: false` would otherwise reject the queue; outside
this area) and teach `cycle.ps1` to read the flag. The shell button reads "Yayını onayla"; it
starts nothing.

**Consequences.** Queue rewrite is JSON with 2-space indent, UTF-8 without BOM, final newline;
formatting differs slightly from the PowerShell writer's (valid, schema-conformant).
