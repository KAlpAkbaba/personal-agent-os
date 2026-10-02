## Şu an üzerinde çalışılan
- Görev: `model-policy-cycle` (geri dönen iş, denetleyicinin 2 maddesi) · alan: `scripts/team/cycle.ps1`, `scripts/lib/TeamQueue.ps1`, `scripts/tests/team-cycle.tests.ps1`, `team/plans/model-policy-cycle-adr.md` · makine: bu PC, worktree `.claude/worktrees/team/d20261001/worker-model-policy-cycle`
- Durum: bitti, commit edildi ve push edildi; worktree temiz.

## Report
**sha:** `0df5976315118a32ddd2d4cc1f7b6f98ff5eb33f` on `team/d20261001/worker-model-policy-cycle` (pushed; remote head matches). 4 files changed, all inside the area.

**Point 1: the unbounded retry on a past reset — fixed.**
- A limit whose reset is already past is believed once per task/role and model, which keeps today's "waited out, run again".
- The second time, what that limit closed is closed for the rest of the cycle, undated, with a line under the risks. The chain then goes one model down, or the existing "ne zaman açılacağı söylenmedi" stop line follows.
- The researcher and the lead's split (`Invoke-RoleRun`) go through the same count.
- The undated mark is not written to `team/limits.json`, so the next cycle asks again, at most two runs.

**Point 2: a worker entry without a model — fixed.**
- New `Get-TeamInspectionFloor` in `TeamQueue.ps1`: the recorded worker model, else the configured worker model.
- It is used for both the inspector's start and the tool-substitution check.
- The wait line says which floor it is: `işçinin modeli kayıtlı değil, ayarlı işçi modeli …`.

**Tests added (2 cycle cases, plus asserts in one unit case), RED before the fix:**
- "a limit whose reset is already past…" with `PAGENTOS_FAKE_CLAUDE_LIMIT_RESET_SECONDS=-600`. RED: 232 runs in the 2-minute hang guard. GREEN, exact run lists:
  - fallback on: `opus, opus, sonnet, inspector=fable`, merged, `failed_runs` 0
  - fallback off: `opus, opus`, then the stop line
  - all three models stale: `opus, opus, sonnet, sonnet`, stop, lock released
  - researcher: `opus, opus, sonnet`
- "the inspector's rule for a worker entry that names no model…". RED: `inspector=claude-sonnet-5-5`, 1 run where 0 was expected. GREEN:
  - plain `tamam` with Fable and Opus limited: 0 runs, task stays `inspecting`, the reason is in the report
  - only Fable limited: `inspector=opus`
  - tool ran the inspection on Sonnet: no verdict, nothing merged

**Mutation RED** (restored from the backup copy; sha256 after restore equals before: `cycle.ps1` `8b017a0e…`, `TeamQueue.ps1` `0ea51671…`):
- stale bound off (`-ge 2` → `-ge 999999`): RED, 240 runs
- floor falls back to `""`: RED in the cycle case and the unit case
- substitution check reads only the recorded model: RED (task merged)
- after restore, the slice is 3 of 3 green

**Suites (PROVEN_AUTOMATED, fakes):** `team-cycle.tests.ps1` 143 passed, 0 failed; `script-syntax` 139 checked, 0 failed; `installer-strictmode` 24 passed, 0 failed.

**NOT_RUN:**
- a real rejection or a real lowering from the tool (no limit is exhausted today)
- `provision.tests.ps1`
- `services/api/tests/unit/test_team_state.py` — outside the area; the inspector saw 1 RED there until `model-policy-api` merges

**ADR:** decisions 9 and 10 added to `team/plans/model-policy-cycle-adr.md`.

**Open risks:**
- When the PC clock runs ahead of the tool's, each cycle spends two runs on the stale model before lowering, because the undated mark lasts one cycle only.
- With all three stale models in "model" scope, a task costs 4 runs before the stop.
- The floor for an old plain `tamam` worker is today's setting; if that worker really ran on a stronger model, nobody knows.
- The inspector's three non-blocking notes are unchanged: the loose "usage limit" regex on a failed run, Fable's `rejected` type on an Opus run marking both, and an inspection holding the lock until Fable's weekly reset under `-WaitForUsageLimit`.

**For the lead at merge:** merge after `model-policy-api`; `.gitignore` needs `team/limits.json` and `team/status.json`; number the ADR addendum.
