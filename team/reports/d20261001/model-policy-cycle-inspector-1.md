**Inspector report: `model-policy-cycle` @ `dd5193b7`** (worktree unchanged; sha256 of `cycle.ps1` `2c767921…` and `TeamQueue.ps1` `bbd6486c…` identical before and after)

**Pass 1: run it**
- `team-cycle.tests.ps1` from a clean tree: **141 passed, 0 failed**. `script-syntax`: 139 checked, 0 failed. `installer-strictmode`: 24 passed, 0 failed. `provision` was not re-run.
- `services/api/tests/unit/test_team_state.py`: 1 failed, 51 passed, as the worker said (`GET /v1/team/queue/models` is not served on this base).
- My mutations, different from the worker's, on a scratch copy of the scripts and restored from backup:
  - The report entry does not record the model: 3 RED.
  - `team/limits.json` is not loaded: 3 RED.
  - The tool-substitution check is off: 1 RED.
  - Restored: 11 of 11 green on the slice.
- Real run through `Get-TeamRunArguments` / `Start-TeamRun` / `Read-TeamRunResult` on `claude-sonnet-5-5`: Ok, `modelUsage` named sonnet, all models 47 %, session 14 %, no Fable window. The real event carried `status: allowed` with `overageStatus: rejected`, which matches the ADR text.
- PostgreSQL: the diff touches no table, migration or store. The 422 on new status fields is real: `StatusRequest` in `services/api/app/team/routes.py` is `extra="forbid", strict=True`. The legacy path itself ran only against the fake.

**Pass 2: break it** (probe cases added to a scratch copy of the suite)
1. **A limit whose reset is already past spins the cycle without bound.** The fake answered "Opus limited, reset 10 minutes ago", fallback on: 123 worker runs on Opus in 60 s, until my timeout killed it. The model is not marked (the reset has passed), so nothing is lowered. `Wait-UsageLimit` waits 0 s, and the try is handed back each time, so `MaxRunsPerTask` never stops it. Each pass also appends a report entry to the task and a "beklendi" line to the report. The worker calls this pre-existing, but the old detector matched none of the real sentences, so the real tool could never reach it; now it can (PC clock ahead of the server by more than 90 s, or a stale `resetsAt`). `Invoke-RoleRun` has a bound of 5; the main loop has none.
2. **A worker entry from before the policy sets no floor.** Task in `inspecting`, worker outcome plain `tamam`, Fable and Opus remembered as limited: the inspector ran on `claude-sonnet-5-5` and the task went to `merged`. Every task whose worker finished before this merges is in that position, and the tool-substitution check is skipped for them too. The owner's rule 4 is silently broken for them.

Held under attack: three parallel workers limited on Opus were each lowered once, nothing counted, all merged, 3 entries in `limits.lowered`. An Ok run after a `rejected` event is not read as limited.

**For the lead, not blocking**
- A failed run whose result text contains "usage limit" or "limit reached" is read as the limit with scope unknown. The run's model is then barred for the cycle and the task lowered with a "model düşürüldü" line. The regex is older than this task; the consequence is new.
- A `rejected` event of Fable's type on an Opus run marks both models until Fable's reset.
- With `-WaitForUsageLimit`, an inspection waiting for Fable's weekly reset holds the cycle and its lock until then when nothing else is runnable.
- Merge after `model-policy-api`. `.gitignore` needs `team/limits.json` and `team/status.json`; `git check-ignore` matches neither today.

**Evidence classes**
- PROVEN_AUTOMATED: the chain, the remembered limit, the inspector's floor, the status fields, the setting's precedence (fakes).
- PROVEN_PROXY: the read half against the real tool (my run); the 422 legacy status (real schema read, fake listener).
- NOT_RUN: a real rejection and a real lowering. No limit is exhausted today; the rejection's shape rests on the binary and the docs.

`RETURN (1: bound the main loop's retry when a limited run's reset is already past or the same model is picked again — treat it as undated and stop with the line, or cap it — with a test using PAGENTOS_FAKE_CLAUDE_LIMIT_RESET_SECONDS=-600 that asserts a small run count; 2: a worker entry without a model gives the floor of the configured worker model, for both the start and the tool-substitution check, with a test where a plain 'tamam' worker and Fable+Opus limited starts no inspector on Sonnet)`
