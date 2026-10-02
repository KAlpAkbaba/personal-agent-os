# Inspector report — `cycle-auto-integrate` @ `44014c88` (fifth inspection)

Both findings of the last return are fixed and hold under a real lead run, a real usage limit and PostgreSQL; nothing I tried put an unchecked file on main.

**Pass 1 — run it** (tree clean; step `7f44dcd8…` and lib `5c7ca579…` identical before and after; nothing left running)
- `team-integrate` 78 passed / 0 failed. `script-syntax` 151/0, `installer-strictmode` 24/0, `provision` 13/0, `team-cycle` 184/0.
- **My mutations** (eleven, scratch copies outside the repo, none the worker's; each checked to change exactly one line): ten RED.
  - RED: gate exit code dropped in `Invoke-TeamGate`; every file allowed in `Test-TeamLeadFileAllowed`; rename detection on; the model a run was on not closed by "out of usage credits"; the no-open-model stop removed; a HEAD outside the branch not refused; `-Model` not filling an unnamed role; "model düşürüldü" not said; `git add -u` instead of `-A`; tree not put back after the environment build.
  - **Survivor:** removing the reset of the tree before the retry one model down leaves both limit tests green. The code itself is right (see the limit probe below); no test holds it.
- **PostgreSQL** (scratch database on the dev stack at `0063_team_state`, real API with `DbStore`, the step over real HTTP, fake gate; database dropped afterwards):
  - The real server answers `GET /v1/team/queue/models` with 404, so the step reads `team/models.json`; with fable in `limits.json` the lead ran on opus, the report said so, and `limits.json` was untouched.
  - Fable and opus "out of usage credits": three runs in one step, down to sonnet, exit 0, one gate.
  - Every model limited, three steps in a row: exit 7 each time, no attempt record, tasks `merged` with the sentence in the row, lock free. The next step with a model open: exit 0.
  - A left-behind child in API mode: stopped, its file not on main, tasks `awaiting_release` with the 40-hex sha and listed at gate `yayin`.
- **Real rehearsal** (scratch clone with its own bare origin; real docker, uv, pnpm; real `claude -p`; mini gate of the real `test_ci_covers_every_suite.py` plus `script-syntax`):
  - Environment 176 + 27 + 189 s. Fable answered "out of usage credits" in 6 s (real `rate_limit_event`, reset 2026-10-05T16:00Z): not counted.
  - Restarted at once on opus: 380 s, 22 Bash calls inside the job object, nothing left behind.
  - Six files wired (`ci.yml`, `DECISIONS.md`, `HANDOFF.md`, `quality-gate.ps1`, `register-nightly.ps1`, `BUILD_STATE.json`); committed diff passed; gate green (7 passed; 151 scripts, 0 failed); main merged `--no-ff` and pushed; exit 0.
- The host-snapshot rule does not apply (no `scripts/cloud`, `infra/docker` or migration).

**Pass 2 — break it**
- A left-behind process that inherits the run's stdout, and an orphaned grandchild (`cmd /c start /b`): both stopped, exit 0 in 5–6 s, nothing on main, the report names the process.
- A writer no job holds (WMI), appending to a tracked file inside the area at 21 different delays: never on main, never on the branch. One run of the 21 ended exit 12: the write landed between the last reset and the checkout for the merge, and git refused. It failed closed with the green record kept; I did not catch it a second time to watch the next run finish it.
- A limit met mid-work, after the run had written inside the area and in `docs/`: both discarded, opus run clean, exit 0, no attempt counted.
- A Turkish file name under `docs/` is refused and counted as `lead_refused` (git quotes it); a name with spaces passes. The worker listed this.
- A `models.json` naming `claude-opus-5-5; calc.exe`: exit 2 before the lock, nothing changed. A half-written `limits.json`: ignored, step goes on. `-DryRun` names the lowered model and changes nothing.
- No release, tag, force or last-known-good name; the token is a file path only.

**Evidence classes**
- Decisions and sandboxed flow: PROVEN_AUTOMATED.
- API mode on PostgreSQL, real tools, a real lead run under the job object, a real usage limit and the lowering: PROVEN_PROXY.
- NOT_RUN: the full `quality-gate.ps1` in a gate worktree (the task is not on an integration branch; it is the lead's gate), the Cloud Core itself, the scheduled task, the Onay Merkezi page in a browser.

## For the lead at merge
- **The branch conflicts with main `f60e02e4` in `docs/HANDOFF.md`**, through your own release record `982dc4fb`; the conflict is not in the worker's files. My rehearsal used `982dc4fb` as main.
- The worker's wiring list stands. The rehearsal's diff is `E:\tmp-insp6\evidence\real-lead-wiring.diff`; that lead again made `-Integrate` an off-by-default switch and numbered the ADR 0252, which main has since used.
- Cut a card for the surviving mutation: a test where the limited run writes before it answers.
- The store's setting is not read in production until `model-policy-api` (on `team/d20261002/worker-model-policy-api`) is merged.
- `.claude/worktrees/gate` is still your own worktree; move it before scheduling.
- `host-snapshot.json` (2026-10-01T19:18Z) is older than the last release.
- 27 `pagentos-integ*` folders are in `%TEMP%`; I did not sort mine from earlier runs'. My server overwrote the stale `E:\tmp-insp\pg-token.txt`; the token died with the database.
- Evidence is in `E:\tmp-insp6\evidence`.

`APPROVE`
