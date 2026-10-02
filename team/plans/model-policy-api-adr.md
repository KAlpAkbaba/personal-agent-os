### ADR-0214 addendum (2026-10-02, model-policy-api): the model policy on the Cloud Core - the setting in the team store, the status' model and limits, the seat's model in the Ofis

Task `model-policy-api`, the Cloud Core's third of the contract in addendum 7. The contract
itself is unchanged; `model-policy-cycle` (the cycle's third) is already on main and calls
`GET /v1/team/queue/models`.

**What the Cloud Core does now.**
* *The setting* is one document in the team store: FileStore `team/models.json`, DbStore one
  `team_state` row `kind='models'`, `key='models'` (no new table, no migration; `updated_at`
  is the document's own stamp, 20 characters in a VARCHAR(32)). `TeamStore.read_models()` /
  `put_models(document)`.
* *`GET /v1/team/queue/models`* answers the setting in force - exactly the three keys `roles`,
  `fallback`, `updated_at`, because the cycle refuses a setting with any other key. Nothing
  stored: the defaults (lead and inspector `claude-fable-5-1`, the rest `claude-opus-5-5`,
  fallback on). A read writes nothing.
* *`PUT /v1/team/queue/models`* replaces it. 422 with the code of the first problem, and
  nothing written, for: `unknown_key`, `unknown_role`, `unknown_model`, `missing_role`,
  `invalid` (not an object, no `roles`, no boolean `fallback`), `inspector_weaker_than_worker`.
  The codes are the ones `Read-TeamModelSetting` (TeamQueue.ps1) uses. `updated_at` is stamped
  by the server; one a client sends back is accepted as a key and not kept.
* *The live status* takes `runs[].model` and `limits` (`fable` / `all`: `state` ok|limited,
  `resets_at`, `used_pct`; `fallback`; `lowered`, at most 20). Anything else is still 422
  (`extra='forbid'`, strict types: `used_pct: "ninety"` is refused). Both are optional: a
  cycle older than the policy is accepted.
* *`GET /v1/team/office`*: every seat has `model` (its role's configured model; the owner seat
  null) and `running_model` only while a live run of that seat is on another model; a run
  entry has `model` when the status named one; `cycle.limits` is the status' limits (ok / null
  percentages when no status gave any); `models` is the setting document.

**Decisions made here, each reversible.**
1. *The rules are one pure module (`models_setting.py`) and the store checks too.* The route
   validates to answer with a code; `put_models` validates again, so no other caller (a
   script, a later route) can store a setting that breaks the rule. A test reads
   `TeamQueue.ps1` and holds its chain, its roles, its defaults and its codes to this module's.
2. *What a store holds is read leniently, and a broken one is the defaults.* `team/models.json`
   in the tree was written by hand before the route existed and has `roles` only: what is
   missing is filled (fallback on). A stored document that breaks the contract (a hand edit:
   an unknown model, an inspector below the worker) is answered as the defaults - a model id
   that is not one of the three never leaves the server, since the cycle puts it on a command
   line. The file-mode cycle reads the same file itself and stops with the reason, so the
   hand edit is not silently lost. Through PUT this cannot happen.
3. *The status is stored as it was sent* (`exclude_unset`): what an old cycle left out is not
   written back as `null`, so the read-back equals the PUT for both shapes.
4. *A model id in the STATUS is a bounded string, not one of the three ids.* The status says
   what the cycle saw - the tool may have run another model (model-policy-cycle decision 6) -
   and a refused heartbeat costs the whole Ofis page its model and limit display for the rest
   of the cycle (the cycle then writes the old form). The SETTING is held to the three ids.
5. *A run entry has `model` only when the status named one.* Null would read as "known to be
   nothing"; an old cycle's run simply has no model, and such a run never produces a
   `running_model`. (It also leaves the run shape `office-worker-seats` pinned unchanged for
   an old-shape status.)
6. *A limit whose `resets_at` has passed is shown as `ok` with a null percentage.* The limits
   are passed through even when the status is no longer live (as `usage_limit` is); without
   this, the last "limited" of a cycle that ended would stand on the page for ever. It is the
   cycle's own rule ("null again once the window's own reset has passed"), applied by the
   reader with its own clock. A limit with no reset is shown as it was written. Goes one step
   past the card's "passed through"; four lines, one test.
7. *What a file holds in another shape never reaches the page as it is*: `cycle.limits` is
   rebuilt key by key (a `used_pct` that is not a number is null; `lowered` is the newest 20).
   The route already refuses such a status; `team/status.json` can be written by anything.
8. *`cycle.limits.fallback`* is the status' when it has one (what the running cycle is
   actually using), else the setting's.

**Evidence.** PROVEN_AUTOMATED: `tests/unit/test_team_models_setting.py` (106 cases, FileStore
and DbStore-on-SQLite) and `tests/integration/test_team_models_postgres.py` (3 cases on the
dev stack's PostgreSQL 16.15: put / read / replace through the store and through the routes,
a status with 20 `lowered` entries, every row within the column widths read from the model).
NOT proven: PROVEN_REAL waits for `model-policy-office-ui` (the owner changes a model on the
page and the next run uses it).

**Rollback.** Additive: no migration, no changed key in an existing answer. Reverting the
commit leaves a `kind='models'` row nobody reads and sends the cycle back to the old status
form by itself (422 -> legacy, model-policy-cycle decision 5).

**For the lead at merge.**
* `services/api/tests/unit/test_team_state.py` (not in this task's area) goes RED on this
  branch, by its own design: `test_every_route_and_body_field_the_powershell_client_uses_is_one_the_server_has`
  says "the server serves it now - remove the entry". Two edits, proven on a scratch copy
  (1 passed): replace the `called_ahead = { ... }` block with
  `called_ahead: dict[tuple[str, str], str] = {}`, and add to `read_by_others`:
  `("PUT", "/v1/team/queue/models"): "the Ofis page writes the owner's choice "
  "(model-policy-office-ui); the cycle only reads the setting",`
* `app/voice/realtime_sessions/tools_team.py` calls `office_view` without `models=`: it gets
  the defaults, and uses none of the new keys today. If the spoken summary ever names a
  model, pass `models_setting.effective(store.read_models(), ...)` as `routes.py` does.
* `docs/TEAM_PROTOCOL.md` / the API reference: two new routes, three new keys in the office
  answer. `team/models.json` needs no change (decision 2).
* Release order: this before `model-policy-office-ui`. No compose change, no migration.
* The dev database was at `0064_memory_vocabulary_class` (the gate branch's) while this tree
  ends at 0063, so the integration suite's `alembic upgrade head` fixture cannot run from this
  branch; the PostgreSQL file was run with `--noconftest` and the suite's advisory lock held
  by a scratch plugin. On the integration branch (which has 0064) it runs as any other file.
