# ADR-0214 addendum (number by the lead): the live status' bounds sit at the route

Task: team-status-bounds (cycle d20261003). Source: model-policy-api inspector report
(team/reports/d20261002/model-policy-api-inspector-1.md, findings 1-4).

## Context

`PUT /v1/team/queue/status` accepted what the store cannot hold or what means nothing - the
shape of ADR-0214 addendum 4 (green on SQLite, a 500 on PostgreSQL):

- `updated_at` (and every other timestamp of the document) was unbounded; `team_state.updated_at`
  is VARCHAR(32): 33+ characters were a 500 on PostgreSQL (`StringDataRightTruncation`),
  a 200 on SQLite and the file store. Reproduced on the dev stack before the change.
- `used_pct: 1e999` was a 500 (the framework's refusal echoed `inf` and could not be written as
  JSON); `estimated_usd` took infinity and NaN; `used_pct` had no range (-5, 250000 accepted).
- The 64-character model id bound worked but no test held it.

## Decision

Four bounds in the request models of `services/api/app/team/routes.py`, nowhere else:

| bound | where | code |
|---|---|---|
| every timestamp <= `STAMP_MAX` = 32 (the column width) | `started_at`, `updated_at`, a run's `started_at`, `usage_limit.resets_at`, both windows' `resets_at`, a lowered's `at` | `status_stamp_too_long` |
| a model id <= 64 | a run's `model`, a lowered's `from` / `to` | `status_model_id_too_long` |
| `used_pct` finite, 0..100 (or null) | `limits.fable`, `limits.all` | `status_used_pct_invalid` |
| `estimated_usd` finite, >= 0 | the document | `status_estimated_usd_invalid` |

The route validates the body itself (`StatusRequest.model_validate`) so a broken bound is
answered as the route's other refusals are: 422 `{detail: {code, message, problems}}`, a Turkish
message from `STATUS_REFUSALS`, the field path in `problems`. Every other broken field keeps the
framework's 422 list, its echoed input made JSON-safe (`pid: 1e999` is a 422, not a 500).
Nothing is written on any refusal. The codes are new; no existing code was renamed.

Why the route: the status is a heartbeat the store keeps as sent; the store, the migration and
the Ofis are right - the request was not checked against the column. A bound at the route
refuses before any store (file or database) is reached, so all three stores answer alike.

## Consequences

- What the cycle writes today is unchanged and accepted: the unit test lifts `New-CycleStatus`
  and `Get-LimitsDocument` out of `scripts/team/cycle.ps1` and runs them under Windows
  PowerShell in seven situations; all seven are a 200 on both stores.
- The cycle's client treats ANY 422 on the status as "an older Cloud Core" and falls back to
  the legacy shape for the rest of the cycle; a bound refusal would do the same. Not triggered
  by today's cycle (20-character stamps, whole-number or tool percentages).
- Open risk for a follow-up card (scripts/ is not this task's area): `TeamQueue.ps1` computes
  `used_pct = round(100 * utilization)` from the tool's events. If the tool ever reports a
  utilization above 1.0 the status is refused (`status_used_pct_invalid`) and the cycle drops
  to the legacy shape for that cycle. A clamp to 0..100 on the client closes it.
- No migration, no setting, no compose change: released automatically under addendum 9.
