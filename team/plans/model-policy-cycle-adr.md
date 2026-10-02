### ADR-0214 addendum (2026-10-01, model-policy-cycle): the model policy in the cycle - the setting from the team store, the chain, the remembered limit, the inspector's floor

Task `model-policy-cycle`, the cycle's third of the contract in addendum 7. The contract itself
is unchanged. Integration plan: `team/plans/model-policy-cycle-integration.md` (tool 2.1.285).

**What the cycle does now.**
* *The setting* is read once, before the lock: API mode `GET /v1/team/queue/models`; a 404 (a
  Cloud Core without the route) or an unreadable answer falls to `team/models.json`, then to the
  defaults (lead and inspector `claude-fable-5-1`, the rest `claude-opus-5-5`, fallback on). It
  never stops the cycle for a missing route. It DOES stop (exit 2, nothing run, no lock) for a
  value that is not one of the three ids, an unknown role or key, or an inspector weaker than
  the worker. Nothing stored used to mean "the tool's own default"; it now means the defaults.
* *A run is started with* `--output-format stream-json --verbose` and
  `CLAUDE_CODE_NO_MODEL_FALLBACK=1`, never `--fallback-model`. The limit's type, its reset as
  an epoch and the two percentages exist only in the stream's `rate_limit_event`.
* *The chain.* A run that comes back with the usage limit marks what the limit closes and the
  SAME task is started again at once on the next open model DOWN (not a failed run, not one of
  MaxRunsPerTask); the run's line says `model düşürüldü: <from> -> <to>` and the status lists
  it under `limits.lowered`. A marked model starts no run until its reset, in this cycle and -
  through `team/limits.json` - the next. No model left, or fallback off: the existing wait
  (known reset) or stop line (unknown), now for the earliest reset among the models the role
  may use. The lead's split run and the researcher go through the same path.
* *The inspector's floor* is the model the task's last finished worker run really used. The
  inspection is started on nothing weaker: a setting below the floor is raised to it, the
  chain stops at it, a stronger open model is taken before waiting, and when every model at
  least that strong is limited the inspection WAITS (`denetim bekliyor: ...` under the risks).

**Decisions made here, each reversible.**
1. *Today's limit detector was wrong against the real tool, and is replaced.* Its regex matched
   none of the six sentences 2.1.285 says ("You've hit your Fable limit ..."), and the string
   it took the reset from is not in the tool at all: a limited run was counted as a failed run
   and the cycle stopped instead of waiting. The reader now takes the result line, the limit
   events and the tool's "I switched the model" notice by their parsed type, line by line (5.1
   refuses JSON over 2 MB), and looks for the limit's words only in the result and in stderr -
   a worker's transcript can hold any sentence.
2. *A session or weekly limit closes every model: no lowering.* Lowering then would start runs
   that hit the same limit and write a false "model düşürüldü". `overage` / "out of usage
   credits" is read as "unknown" - the model that ran is marked and the chain finds out the
   rest - where the integration plan said "all": the owner's account answers
   `overageStatus: rejected` on every ordinary run, so the words do not say whose limit it was,
   and two doomed starts cost less than a needless wait of hours.
3. *The chain goes down only.* A worker whose Opus and Sonnet are limited is not sent UP to
   Fable: the strongest model's limit is the scarce thing (addendum 7). Only the inspector may
   go up, because its rule is "at least as strong as the worker".
4. *The worker's real model is kept in the report entry's `outcome`* (`tamam (model <id>)`),
   written and read by one pair of functions. The card says "the worker's report entry records
   its model"; a `model` FIELD would be refused by the queue's schema (`additionalProperties:
   false`, `team/queue.schema.json`, not in this task's area) - on the serving Cloud Core every
   task write would be 422 and the all-day cycle would die. `outcome` is free text, travels
   with the task to the other machine, and no reader compares it. If the three cards agree on
   a field, the pair of functions is the only place to change.
5. *An older Cloud Core still gets a status.* The serving status route forbids unknown keys, so
   the new `runs[].model` and `limits` are 422 there until `model-policy-api` is released. The
   cycle then writes the form that Cloud Core knows for the rest of the cycle and says so once
   under the risks; without this the Ofis page would read "no cycle" the moment this merges.
6. *The rule holds against the tool too.* If `modelUsage` shows the tool ran an inspection on a
   model weaker than the worker's, no verdict is taken, the model asked for is treated as
   limited for this cycle, and the inspection is started again on a model at least as strong
   or waits. The run's line says `model düşürüldü (araç): <asked> -> <ran>`. (Goes past the
   card's acceptance; the integrator raised it. It is ten lines and closes the one way an
   approval could come from a weaker judge.)
7. *The two percentages* are `seven_day_overage_included.utilization` (Fable) and
   `seven_day.utilization` (all models) of the last event that carried them, shown as the
   tool's own number times one hundred. Null until a run gave one; null again once the window's
   own reset has passed. The Fable number is only as fresh as the last run ON Fable.
8. *`team/limits.json` is machine-local*, written beside the queue files in both store modes. A
   limit nobody dated is not written to it: on disk it would bar the model for ever.

9. *A limit whose reset is already past is believed once per task.* Such a limit marks nothing
   (its reset has passed), the wait is 0 s and the try is handed back, so the main loop had no
   bound: the inspector's probe gave 123 worker runs in a minute. The old detector matched none
   of the real sentences, so the real tool could not reach this; the new one can (a PC clock
   ahead of the tool's, a stale `resetsAt`). The first such answer is today's "waited out, run
   again". When the SAME run (task and role) gets it a second time on the same model, the hour
   it names is not taken as the truth: what the limit closed is closed for the rest of the
   cycle, undated, with a line under the risks. From there the existing paths apply - the chain
   goes one model down, or (fallback off, nothing left) the stop line of a limit nobody dated.
   Counted per task, so parallel runs that each meet a limit which has just lifted are each
   retried once. Undated is not written to `team/limits.json`: the next cycle asks again, at
   most two runs. `Invoke-RoleRun` (researcher, the lead's split) goes through the same count.
10. *A worker entry that names no model gives the floor of the configured worker model.* Every
   task whose worker finished before this merges has a plain `tamam`; "no floor" let its
   inspection run on Sonnet with Fable and Opus limited, and skipped the tool-substitution
   check. One function (`Get-TeamInspectionFloor`) now answers both the start and that check;
   the wait line says which floor it is (`işçinin modeli kayıtlı değil, ayarlı işçi modeli ...`).
   If the old worker really ran on something stronger than the setting says today, nobody
   knows it: the setting is the best statement there is.

**Found on the way.** `scripts/tests/lib/fake-claude.ps1` never received `--output-format`:
PowerShell bound the tool's `-p` to its own `-PipelineVariable` and swallowed the next
argument. Nothing read it until now. The fake takes `$args`. `fake-team-api.ps1` logged an
error answer without its method.

**Evidence.** PROVEN_AUTOMATED: `scripts/tests/team-cycle.tests.ps1`, the "model policy" cases,
against fixtures in the real tool's format. PROVEN_REAL for the read half only: one real run on
`claude-sonnet-5-5` through `Get-TeamRunArguments` / `Start-TeamRun` / `Read-TeamRunResult`
read the result, `modelUsage` and both windows (all models 47 %, no Fable window). The
rejection itself has not been seen from the real tool: its shape is from the binary and the
documentation, and the first real lowering is the proof.

**Rollback.** `"fallback": false` in the setting restores the wait without a code change;
`team/limits.json` can be deleted at any time; `Get-TeamRunArguments` back to
`"--output-format","json"` still reads, through the single-document branch.

**For the lead at merge.**
* Merge AFTER `model-policy-api`, or accept one RED: `services/api/tests/unit/test_team_state.py`
  holds every route `TeamQueue.ps1` calls to the server's, and `GET /v1/team/queue/models` is
  not served on this branch's base.
* `docs/THIRD_PARTY_COMPONENTS.md`: the entry text is in the integration plan.
* `.gitignore`: `team/limits.json` (new) and `team/status.json` are machine-local.
* `team/models.json` in the tree has no `fallback` / `updated_at`; the defaults fill them.
* `team/queue.schema.json`: nothing needed (decision 4). `docs/TEAM_PROTOCOL.md` section 3
  could name the chain; this task did not touch it.
