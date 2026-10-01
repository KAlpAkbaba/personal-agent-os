# ADR (no number yet): merged work is gated and put on main by a step, not by a person

Status: accepted by the worker of `cycle-auto-integrate`; the lead numbers it and moves it into
`docs/DECISIONS.md` at merge time. Serves ADR-0214 addenda 6 and 8 (TEAM_PROTOCOL 3, 4, 9a, 10)
and the roadmap row "Repairs and improves itself (kept controlled)".

## Context

The cycle starts every 30 minutes but ENDS at "merged into `integrate/<cycle-id>`". The full
gate on that branch and the merge to main were the lead's, by hand. A task whose `depends_on`
is not on main waits (`Get-TeamUnmetDependencies`), so a task finished at noon unblocked nothing
until a person had time. Owner, 2026-10-01: "sürekli, kontrollü olması gerekiyor."

## Decision

`scripts/team/integrate.ps1` (decisions in `scripts/lib/TeamIntegrate.ps1`) is a SEPARATE step
the scheduled task runs after the cycle. It never releases, makes no tag and names nothing of
production, the recovery supervisor or the last-known-good record (a test reads its text).

1. **What it takes.** Tasks in `merged`, grouped by `integration_branch`, whose branch is ahead
   of main, that no returned task holds and whose tip the gate was not already red on (point 7).
   Nothing else: no lock, no report, exit 0.
2. **The lock** is the cycle's (file or API, the same functions), held as
   `integrate-<branch>` and released in `finally`. The other machine's lock stops it before
   anything is written.
3. **Where.** `.claude/worktrees/gate/<branch>`, on a DETACHED HEAD: git allows a branch in one
   worktree only, and `integrate/<cycle>` already lives in the cycle's own. It is this step's
   scratch tree and the only place that is ever reset. main is merged in first; a conflict is
   aborted and the tasks go to `stopped` with `main ile çakışma: <files>`.
4. **The lead's wiring run** gets, per task, the "For the lead at merge" section of the newest
   worker and inspector report (the report file when it is there, else the queue's forty
   lines). The SCRIPT judges its diff, the model does not: allowed are `docs/`, `.github/`,
   `team/`, `scripts/quality-gate.ps1`, `state/BUILD_STATE.json`, and a file a section NAMES as
   a path (whole, or its last directories; a bare `main.py` opens nothing). One file outside
   that refuses the run whole: the tree is reset, nothing is merged. What passed is committed
   and the integration branch is fast-forwarded to it, so what is gated IS the branch.
   **The run has Bash and shares the repository**, so the worktree's diff is not all it can
   change: `refs/heads/<Base>`, the integration branch and `refs/remotes/<Remote>/<Base>` are
   read before and after the run. One that moved (a commit made on main from the gate worktree
   while main is checked out nowhere; a push) refuses the run with exit 13, is named with both
   shas in the report, the console and every task's reason, and stops the branch AT ONCE (not
   at the second attempt: a second attempt would merge the moved main in, gate it and push it).
   The step does NOT put the ref back: it cannot tell the run's move from a person's during the
   same minutes, and it never moves a branch backwards. The report carries the
   `git update-ref <ref> <before> <after>` line for the lead. Only these three refs are watched:
   the lead's own session commits and makes branches in the main checkout all day, and watching
   every ref would refuse runs for that.
   Two additions to the card's list, both the most restrictive reading:
   `state/BUILD_STATE.json` is allowed (TEAM_PROTOCOL 4 makes it the lead's);
   `team/queue.json` and `team/lock.json` are refused although they are under `team/` (they are
   the cycle's; a committed held lock stops every cycle for six hours).
5. **The gate** runs in that worktree after `uv sync` (services/api, services/browser) and
   `pnpm install --frozen-lockfile --prefer-offline` (at the root, where pnpm's lock file is);
   the report says what each took. docker, uv and pnpm are resolved as an `.exe` or a `.cmd`
   only - PATH folder by folder, then the fallbacks - never through `Get-Command`, which answers
   `pnpm.ps1` on this machine, a file a process cannot start. What the gate leaves changed in
   its worktree is discarded before the merge for main is made there. Docker is probed first (`docker info`): down means
   `Docker çalışmıyor` and nothing changes. GREEN needs BOTH exit code 0 and the gate's last
   word `QUALITY GATE: PASS`; a failed step is read from the `FAILED: ` line of the gate's
   `Invoke-Step` (the summary table is cut at the console's width). The log is
   `team/reports/<cycle>/gate-<n>.log`, written as the gate runs.
6. **Green.** main gets `git merge --no-ff` of exactly the gated commit (message names the sha;
   the merge's tree must equal the gated tree), is pushed, and the tasks become
   `awaiting_release` with main's sha. main is only moved FORWARD: by `merge --ff-only` in the
   worktree that has it checked out (git refuses if the owner's uncommitted work is in the way),
   or by a compare-and-swap `update-ref` when no worktree has it. Green is recorded before main
   is touched, so a run that dies, or finds main blocked, is finished by the next one WITHOUT a
   second gate.
7. **Red.** Nothing reaches main. The failing steps and the first failing test go into the
   report and each task's `reason`; a task is `returned` when the failing steps' text holds the
   path of a file its branch changed (whole, or at least two last segments - how pytest prints
   it) AND that file is inside the task's area - a worker's branch is opened from the cycle's
   `-Base` (the lead's branch, which can be ahead of main), so its diff against main also holds
   files that are not its own. The others stay `merged`. A gate-return does not count towards
   the inspector's two returns.
   **A returned task's code stays on the integration branch, so the branch goes onto main WHOLE
   or not at all.** While any task with `integration_branch` = this branch is not in `merged` /
   `awaiting_release` / `released` / `awaiting_real_evidence` / `done`, nothing of the branch is
   gated: a green gate would put the returned task's code on main while its task says
   `returned`. The tasks that stay `merged` wait, and their reason names whom they wait for. The
   worker's fix is merged into the same branch by the cycle, the task is `merged` again, the tip
   is new, and ONE gate judges everything. Reverting the returned task's merge out of the branch
   was rejected: the fixed branch could then only come back through a revert of the revert, and
   a step that rewrites what the cycle merged is no longer "nothing is forced".
   `-ClearGateStop` does not open a held branch; the lead's way out is the task's own state.
   **A commit the gate was red on is not gated again.** When the last attempt is `red` on the
   branch's tip and main is contained in it, the step waits (no lock, no lead run, exit 0) for
   a new tip - a fix merged in, main moved - or for `-ClearGateStop`, which gates it once more
   (a flake, a service that was down). The second hour of a known answer is not spent.
8. **Two failed attempts in a row stop the branch** (TEAM_PROTOCOL 10) until the lead runs
   `-ClearGateStop`. A refused lead run and a lead run without a result count as attempts (they
   would otherwise be retried every half hour, a lead run each); the usage limit, a missing
   environment and a blocked main do not. A moved ref (point 4) stops it at the first.
   The count lives in `team/reports/<cycle>/gate-<n>.json`
   because the queue's schema has no field for it and this task may not change the schema.

## Consequences

- A dependency reaches main, and its dependants start, without the lead's hands.
- **Known tension, for the lead to decide:** the lock is held for the whole gate, as the card
  asks, so no cycle starts on this machine while a gate runs - against "the cycle is never
  paused for the lead's gate" (addendum 8). Releasing it during the gate is safe only in API
  mode (per-task versioned writes); in file mode the step would overwrite the cycle's queue.
- The suite must be added to `scripts/quality-gate.ps1` and `.github/workflows/ci.yml`, and the
  call to `scripts/team/register-nightly.ps1`, by the lead (outside this task's area).
- `-Base` (default `main`) is the branch that RECEIVES the gated work. While the cycle opens
  worker branches from `team/nightly/lead`, an integration branch carries that branch's commits
  too, and the first green gate puts them on main with the tasks. The lead decides whether that
  is wanted before scheduling the step.
- One slow task holds its whole branch: with one integration branch a day, a task returned by
  the gate at noon keeps the day's other merged tasks off main until it is fixed and merged
  again. That is the price of never putting unpassed code on main; the lead can take a task out
  by hand.
- After a moved ref the ref stays where the run left it until the lead looks. If it was main,
  main holds a commit no gate saw; this branch is stopped, but ANOTHER integration branch that
  goes green would merge and push that main. Not closed by this task.
- After a failed push the tasks are `awaiting_release` and nothing retries the push until
  another branch goes green (the report and exit code 9 say it). Not closed by this task.
- `cycle.ps1` still writes "tam kapı ve main'e birleştirme bu betikte yok; lead yapar" under
  the protocol gaps; that line is stale once the step is scheduled.
- Evidence: PROVEN_AUTOMATED with fakes (sandbox repository, fake gate, fake lead, fake API,
  fake docker/uv/pnpm). The real gate, the real lead and real `uv`/`pnpm` in a gate worktree
  have NOT been run by this task.
