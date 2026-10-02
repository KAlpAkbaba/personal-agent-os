## Şu an üzerinde çalışılan
`model-policy-cycle` (worker, cycle d20261001) — area: the cycle scripts, the two fakes, the cycle tests, two plan files — machine MAIL. Done; nothing in flight.

**sha:** `dd5193b732456eb736dab14b33fb75dfa35e0a0d` on `team/d20261001/worker-model-policy-cycle`, pushed, worktree clean.
**Files changed:** 8, all inside the area (the integrator's plan was untracked in the main checkout; committed here byte-identical).

**What it does now**
- **Setting:** read from `GET /v1/team/queue/models`; a 404 falls to `team/models.json`, then the defaults. An id outside the three stops the cycle with exit 2, before the lock.
- **Chain:** a limited run restarts the same task at once one model down. It is not a failed run and not one of MaxRunsPerTask. The run line says `model düşürüldü: <from> -> <to>` and the status lists it under `limits.lowered`.
- **Remembered limit:** a limited model starts no run until its reset, in this cycle and the next (`team/limits.json`).
- **Inspector:** never started below the model the worker's run really used. If every model at least that strong is limited, it waits and the report says `denetim bekliyor: …`.
- **Lead split and researcher:** same chain.
- **Status:** each run carries `model`; `limits` is as the contract says. `used_pct` is the tool's own number or null.

**Tests** (`scripts/tests/team-cycle.tests.ps1`)
- RED first: 22 of 22 new or changed cases failed on the old code (the limited run was counted as a failed run; the inspector was started).
- GREEN: 141 passed, 0 failed (120 before). `script-syntax` 139 checked, 0 failed; `installer-strictmode` 24 passed; `provision` 13 passed.
- One case (lead split and researcher) was written after the code; its RED is mutation B.

**Mutation RED** (restored from a backup copy; sha256 identical before and after: cycle `6281b0f4…`, TeamQueue `bbd6486c…`; a header comment was added to `cycle.ps1` afterwards, then the full suite was rerun)
- **A, inspector floor not passed in the cycle:** 1 failed (inspector started on Opus and Sonnet, expected 0 runs).
- **A2, the model picker ignores the floor:** 2 failed.
- **B, limited model not remembered:** 5 failed.

**Evidence**
- PROVEN_AUTOMATED: everything above, on fakes with fixtures in the real tool's format.
- PROVEN_REAL, read half only: one real run on `claude-sonnet-5-5` through the cycle's own start and read functions. It read the result, `modelUsage` = sonnet, all models 47 %, no Fable window.
- NOT_RUN: a real rejection and a real lowering. No limit is exhausted today.

**Decisions beyond the card** (ADR text in `team/plans/model-policy-cycle-adr.md`)
- The worker's model is kept in the report entry's `outcome` text (`tamam (model <id>)`), not in a new field. The queue schema forbids unknown fields, so a field would be a 422 on every task write to the Cloud Core.
- A Cloud Core that rejects the new status fields (422) gets the old status form, with one risk line. Otherwise the Ofis page would go dark until `model-policy-api` is released.
- "Out of usage credits" / `overage` marks only the model that ran, where the plan said all models.
- If the tool itself ran an inspection on a model weaker than the worker's, no verdict is taken (the integrator's test 5).
- With nothing stored, runs now get the contract's defaults instead of the tool's own default.

**Bugs found and fixed on the way**
- The old limit detector matched none of the real sentences and never got a reset time.
- The fake model lost `--output-format` to PowerShell's `-PipelineVariable`.
- The fake API logged error answers without the method.

**For the lead at merge**
- `services/api/tests/unit/test_team_state.py` has 1 failed, 51 passed on this branch: `GET /v1/team/queue/models` is not served yet. Merge after `model-policy-api`.
- `docs/DECISIONS.md` gets the ADR text; `docs/THIRD_PARTY_COMPONENTS.md` gets the entry in the plan.
- `.gitignore`: `team/limits.json` (new) and `team/status.json` are machine-local.

**Open risks**
- The rejection shape comes from the binary and the docs, not from a real run.
- A tool that reports a reset already in the past while still limited would be retried without a pause; this existed before.
- A session limit now waits at once instead of trying lower models.
