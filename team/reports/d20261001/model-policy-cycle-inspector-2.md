# Inspector report — model-policy-cycle @ `0df59763` (second inspection)

**Pass 1 — run it (worktree clean before and after)**
- `team-cycle.tests.ps1`: 143 passed, 0 failed. `script-syntax`: 139 checked, 0 failed. `installer-strictmode`: 24 passed, 0 failed. All three match the worker's numbers.
- The worker's two commits touch only the eight files of the area.
- Both returned points are fixed and covered: the stale-reset case and the "worker entry without a model" case are green.
- `services/api/tests/unit/test_team_state.py`: 51 passed, 1 failed. The failure is the expected one: `GET /v1/team/queue/models` is not served on this base, and no `model-policy-api` branch exists locally yet.

**My mutations** (on a scratch copy, 30/30 baseline on the model slice; restored by sha256 `0ea51671…` / `8b017a0e…`):
- Floor cap on the chain removed in `Get-TeamRunModel`: RED, 3 cases.
- `Save-Limits` writes nothing: RED, 3 cases.
- `Test-TeamModelLimited` always false: RED, 8 cases.

**Real runs**
- **Real tool, read half (PROVEN_PROXY):** one run on `claude-sonnet-5-5` through `Get-TeamRunArguments` / `Start-TeamRun` / `Read-TeamRunResult`. It read `Ok=True`, ran model `claude-sonnet-5-5`, not substituted, all-models window 54 %, session 9 %, Fable null. The result line starts with `{"duration_api_ms"`, as the reader assumes.
- **Real status route (PROVEN_PROXY):** the real `StatusRequest` refuses the new document (`runs[].model` and `limits` are `extra_forbidden`) and accepts the legacy form the cycle falls back to. ADR decision 5 holds against the real code.
- **PostgreSQL rule:** not applicable. The diff touches no table, migration, store, broker, container or scheduler; only the fake API changed.
- **NOT_RUN:** a real rejection or a real lowering (cannot be provoked today), and `provision.tests.ps1`.

**Pass 2 — break it (probes with the fake tool; none blocks)**
1. **The floor is the last worker run, not the strongest.** A task with worker entries Fable then Sonnet (rework after a RETURN on a lowered model), with Fable and Opus limited, was inspected on Sonnet and merged. The ADR documents "last finished run", but a weaker judge then approves a branch that is mostly the stronger model's work. Recommend a follow-up card: take the strongest of the task's worker runs (one line in `Get-TeamWorkerModel`).
2. **A waiting inspection stops the whole cycle the first time.** With `task-one` waiting on Fable and `task-two` runnable on Opus, at `-MaxParallel 1` the cycle made one call, wrote the stop line, and never started `task-two`. The code comment says "the others of the queue go on"; that is true only once the limit is in `team/limits.json`. With the default `-WaitForUsageLimit` the cycle would instead sleep, holding the lock, until the reset (not run). This is the same root as the earlier third note and conflicts with addendum 8 (never idle).
3. **A hand-edited `team/limits.json` kills every cycle.** With `until` set to a non-time for all three models, the cycle dies with a `FormatException`, starts nothing, exits 0, releases the lock, and leaves the file as it was. Only a hand edit reaches this; the script itself writes valid stamps.
4. **Cosmetic:** with fallback off, the wait note says "en az o kadar güçlü modeller limitte (fable)" while Opus is open. The real reason is that fallback is off.
5. The three earlier non-blocking notes (loose "usage limit" regex on a failed run, Fable's `rejected` type marking both models, the lock held until the weekly reset) are unchanged, as the worker says.

No secrets or machine paths in the diff. A model id is checked against the three ids before it reaches a command line. Rollback is `"fallback": false`, or delete `team/limits.json`.

**For the lead at merge**
- Merge after `model-policy-api`, or accept the one RED above.
- `.gitignore` has neither `team/limits.json` nor `team/status.json`; the cycle writes both in the main checkout.
- Number the ADR addendum.
- Decide findings 1 and 2 as follow-up cards.

**Evidence:** PROVEN_AUTOMATED (fakes) for the chain, the remembered limit, the inspector rule and the status; PROVEN_PROXY for the read half and the legacy-status fallback; NOT_RUN for a real lowering.

APPROVE
