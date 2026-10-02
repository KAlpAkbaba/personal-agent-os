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

9. **Third round (inspector's second return, 2026-10-02) - five rules that replace what points
   4, 5, 7 and 8 said where they differ.**
   - **The lead-diff check reads moves as what they are.** `git diff --name-only --no-renames`:
     with rename detection a file moved out of a task's area into `docs/` was listed by its new,
     allowed name only and the deletion in the area rode to main on a green gate.
   - **A red verdict survives a failed queue write.** The attempt's record now carries the
     verdict (`blamed`, `reason`, `waits`) and `applied: false`; it is set to `true` only after
     the queue was written. A run that finds the LAST record red on the branch's tip and not
     applied writes the verdict (under the lock, no Docker probe, no lead run, no gate) and
     exits 6 or 8 - before the "held" and "already red" waits are even asked. The write stays
     the STRICT one (`Save-TeamQueueApi` without `-SkipStale`): the next run reads the store
     again, so its write is on the fresh version and the other writer's change is kept. A record
     without the mark (made before it existed) is never re-applied. `-ClearGateStop` asks for a
     new gate instead.
   - **The environment is built BEFORE the lead's run**, so a broken `uv`/`pnpm` costs no model
     run however often the step comes back (exit 10, nothing counted - nothing was paid for).
     The tree is then put back on the commit: what a build scribbles on a tracked file (a lock
     file) is neither held against the lead's run nor gated. When the wiring changes a file the
     environment is built from (`pyproject.toml`, `uv.lock`, `pnpm-lock.yaml`, `package.json`,
     `pnpm-workspace.yaml`) it is built again on the wired commit; a failure THERE is a
     `lead_failed` attempt, counted, and the integration branch is not moved. Any other error
     after a lead run was started is recorded as `error` and counted too: two stop the branch.
   - **Blame comes from FAILING lines of FAILING steps only** (`Get-TeamGateFailingLines`): a
     line that begins with a failure's mark and the indented lines under it, a line holding a
     place in a file (`path:line`, `path(line,col)`), a compiler's `error`, pytest's progress
     line with an F or an E. A line that says PASS is never one. A gate that died (no step said
     `FAILED: `, a timeout) names NOBODY: its tasks stay `merged` with the reason, and the
     commit waits for a new tip or the lead's `-ClearGateStop`. The price is the other
     direction: a failure whose lines carry no mark this reader knows returns nobody, and the
     lead looks. That is the cheaper error - a wrong return costs a worker run and an inspector
     run per innocent task.
   - **The step never runs uncapped.** `-GateMinutes` (default 150) and `-LeadMinutes` (default
     30) must be more than 0 and at most 240 together; each of the three parts of the
     environment's build is capped at 15 minutes (built twice at most). Worst case 5.5 hours,
     inside the lock's six-hour takeover - a
     later run can no longer reset the worktree under a live gate. This departs from "no time
     cap on a run" (owner, 2026-09-30) for this step only, because this step holds the team
     lock while it waits; a gate killed at its cap is red, names nobody, and is said.

10. **Fourth round (inspector's third return, 2026-10-02).**
    - **A gate worktree deleted by hand is made again.** The step never removes its trees (a
      gigabyte each), so somebody will; git keeps the record and `git worktree add` then refuses
      the path - every run ended 12, with no strike and no reason on the task. Now
      `Reset-TeamGateWorktree` removes THAT path's record (`git worktree remove <path>`, which
      git allows for a missing folder) and adds the tree again. Not `git worktree prune`: that
      clears every missing worktree's record in the repository, and the others are not this
      step's (a test holds that another missing worktree stays registered). An EMPTY folder
      that is left is taken. A folder that is left WITHOUT its `.git` and not empty (a delete
      that stopped at an open file) is not deleted by the step - it cannot tell the folder is
      its own - and the run stops with the folder's name and "delete it by hand".
    - **A run that started nothing writes no report.** Stopped by the lock (exit 3) or by Docker
      (exit 4), the step used to write `team/reports/<cycle>-integrate.md` and post it: half an
      hour after a red gate the Onay Merkezi showed "kilit başka koşuda" in place of the gate's
      words. Now such a run writes and posts nothing for the branch - in API mode it sends GETs
      only - and leaves one line (time, machine, branches, the sentence) in
      `team/reports/integrate-skipped.log`, local, newest 200 kept. `cycle.ps1` still overwrites
      its own report in the same case (outside this task's area).
    - **main moving while the gate runs** was right and untested: both guards (the "main is
      contained in what was gated" check and the tree-equality throw) could be removed with the
      suite green. A test now moves main from the fake gate's hook: exit 11, main is the other
      writer's commit and nothing else, nothing pushed, the tasks stay `merged` with the reason,
      and the next run merges the new main in and gates AGAIN.
    - **The tree-equality guard was dead, and is not any more.** Writing that test's mutations
      showed it: with the first guard removed, the merge still reached main. The guard compared
      `Get-TeamRevision "<sha>^{tree}"` on both sides, and that function asks git for a COMMIT -
      a tree is "" through it, and "" equals "". `Test-TeamSameTree` reads the trees themselves
      and answers false when either cannot be read. Given the first guard the second cannot
      fire (a merge of a descendant onto its ancestor has the descendant's tree), so it is
      proven by its own test and by the mutation: first guard removed, the step now ends 12 with
      main untouched instead of merging.

11. **Fifth round (inspector's fourth return, 2026-10-02): the first REAL lead run.**
    - **Only what the diff check saw is committed.** The real run ended with "tests are running in
      the background; I will write the report when they finish". The check read the tree once and
      `git add -A` then committed whatever was there: a file written in between reached main in
      2 of 10 runs. Three rules now, each with its own test and its own mutation:
      (a) the run's WHOLE process tree is stopped when its main process ends (or its cap is
      reached), before a ref, the result or a file is read. The run is put in a Windows job
      object the moment it is started; the job is terminated and the step waits until it is
      empty. `taskkill /T` does not do this: it finds children through a parent that is alive,
      and here the parent is gone. The output pipes are read after the stop, so a process that
      kept the run's stdout open no longer turns a finished run into "no result". The report
      says how many processes the run left and their names;
      (b) the allow-list is held against the COMMITTED diff (`git diff --name-only --no-renames
      <before the run> <the commit>`), not against the working tree before staging: what is
      checked is, by construction, what is gated. A writer the job does not hold (started
      through a service, a scheduled task) that writes before the commit is refused with the
      file's name - a `lead_refused` attempt;
      (c) the tree is put back on the commit before the gate runs, so a file written after the
      commit is neither committed nor gated on (the inspector's other 8 of 10).
      The lead's card now says: wait for every command you started, start nothing in the
      background; what is still going when the run ends is stopped and never committed.
    - **The step follows the model policy (TEAM_PROTOCOL 9a; ADR-0214 addenda 7, 10, 13).** The
      setting is read where the cycle reads it - `GET /v1/team/queue/models` in API mode, else
      `team/models.json`, else the defaults, through the same `Read-TeamModelSetting` - and
      `team/limits.json` is read (never written: it is the cycle's file). The lead's run starts
      on the lead's model, or - when that one is limited and `fallback` is on - on the next open
      model down (`Get-TeamRunModel`); the report says `model düşürüldü: <from> -> <to>`. With
      no open model no run is started and the environment is not built. A run that answers with
      the usage limit closes its model (or every model: a session or weekly limit) for the rest
      of the step, the tree is put back, and the same wiring run is started again AT ONCE one
      model down - one try per model of the chain, all under the one `-LeadMinutes` cap.
      **A usage limit is never an attempt** (no record, no strike, exit 7 every time) and **the
      step never waits for a reset**: it holds the team lock, and the next scheduled run asks
      again (a test holds that `integrate.ps1` has no sleep at all). `-Model` is, as in the
      cycle, the model of a role the setting does not name, and must be one of the three ids.
      What the step learns about a limit lives for that step only; the next one finds out
      again in one five-second run unless a cycle has written it to `team/limits.json`.

12. **Sixth round (the lead's return at the merge, 2026-10-02): the run is inside its job BEFORE
    it can start anything.** Rule 11 (a) said "the moment it is started"; the code started the
    run (`Start-TeamRun`) and put it into the job on the NEXT line. Under load (the gate's unit
    suite and six agent runs) the step was descheduled between the two lines: 1 of 78, the
    left-behind process wrote. The lead's reading is proven, not taken on trust: with the step
    held between the two lines (a seam, below) the old code fails in every run, two ways -
    a process the run started in between is in no job; and a run that had already ended could
    not be assigned at all (`Held = false`, `Why = ""`), after which nothing was stopped.
    - **Chosen: `CreateProcess` with `CREATE_SUSPENDED`, assign, resume**
      (`Start-TeamHeldRun`, `scripts/lib/TeamIntegrate.ps1`). The command exists and has not run
      one instruction when `AssignProcessToJobObject` is called; there is no "in between" left
      to be fast or slow in. **Not chosen: a launcher** the step starts, assigns and then
      releases through an event or a file. It closes the same window but adds a second process
      and a hand-over protocol between the step and the real command: the launcher must pass
      the prompt on standard input and both output pipes through unchanged (the result
      document is read from them, UTF-8, the usage-limit line included), its own exit code
      must become the command's, and a launcher that dies leaves a release nobody answers -
      each of them a new way to fail in the one step that puts code on main without a person.
      The suspended start has no protocol: one kernel call more, in the same process.
    - **What it costs.** `System.Diagnostics.Process` cannot start suspended, so the three
      pipes and the `CreateProcess` call are made in the step's own C# (thirty lines beside the
      job object's declarations): inherited handles, no window, the caller's environment plus
      `CLAUDE_CODE_NO_MODEL_FALLBACK`, the same command line `Process.Start` builds. The run
      object keeps `Start-TeamRun`'s shape, so `Wait-TeamRun` (not this task's file) reads it
      unchanged. The prompt is written off the step's thread: a command that never reads its
      input no longer holds the step past `-LeadMinutes`.
    - **No job, no run.** When the command cannot be put into a job (or cannot be resumed), it
      is ended as it was created - it never ran - and the step starts NO lead run: tasks stay
      `merged` with "lead koşusu başlatılmadı - süreç ağacı tutulamadı (...)", exit 7, no
      attempt record, no strike, nothing paid for; the next step tries again. The earlier
      behaviour (run on unheld, with a line under the risks) is gone, and so is
      `Start-TeamRunJob`: there is no function that puts a running process into a job.
    - **The seam.** `PAGENTOS_TEAM_INTEGRATE_HOLD_BEFORE_JOB` names a file: the step waits just
      before the assignment until the file exists (ten seconds at most) and writes
      `<file>.passed` after it; `PAGENTOS_TEAM_INTEGRATE_JOB_FAILS` makes the assignment fail.
      Both only ever delay or REFUSE a run - neither can make one go unheld. Unset outside the
      suite. The two cases held there cost ten seconds each when green (the suspended command
      cannot create the file).
    - Rules (b) and (c) are unchanged and still hold what no job can (a WMI-started writer): the
      three cases of round five are unedited.
    - **Evidence of this round.** The three new cases were RED on the old code with the seam
      between its two lines, and are RED again under two mutations (the command resumed before
      the assignment: both held cases; a failed assignment ignored: the refusal case). The case
      that failed at the merge ran 30 times beside three other suites in a loop (and, for most
      of it, the whole of this suite and other workers' suites): 30 of 30 green. The real
      `claude.exe` (`--version` only) starts suspended, is in its job before it runs, and its
      pipes and exit code are read: PROVEN_PROXY for the start; a real wiring run through the
      new start has NOT been run by this task.
    - Not closed here: the job has no kill-on-close limit, so a step that is itself killed
      mid-run leaves the lead's run going (as before this round).

## Open decisions for the lead (not built by this task)

1. **The lock is held for the whole gate** (up to 150 minutes by default), as the card asks, so
   no cycle starts on this machine while a gate runs - against rule (c), "the cycle is never
   paused for the lead's gate" (ADR-0214 addendum 8). Options: (a) keep it, and accept that a
   gate pauses the cycle; (b) release the lock after the wiring commit and take it again for
   the merge - safe only in API mode (per-task versioned writes; a stale write is already
   recovered by the next run), in file mode the step would overwrite the cycle's queue; (c) a
   lock of the step's own, so two gates never overlap but the cycle is not held. The worker's
   reading: (b) or (c), API mode only, as a task of its own with its own tests.
2. **`-Base main` while worker branches open from `team/nightly/lead`.** `-Base` is the branch
   that RECEIVES the gated work. An integration branch carries the lead branch's commits too,
   so the first green gate puts them on main with the tasks, unreviewed as a set. Options: (a)
   schedule the step only once the cycle's `-Base` is main; (b) schedule it with
   `-Base team/nightly/lead` and keep the merge to main the lead's; (c) accept it - the lead
   branch's commits pass the same full gate. The step does not choose; the default stays main.
3. **The wiring run may edit the `scripts/quality-gate.ps1` it is then judged by.** The file is
   on the allowed list because adding a suite to the gate is the wiring. Nothing stops a run
   from REMOVING a step: the gate would be green on less. Options: (a) accept it and read the
   wiring commit's diff of that file in the report (it is listed under "lead'in bağladığı
   dosyalar"); (b) run the gate script of `-Base` (main's copy) plus the suites the reports
   name; (c) refuse a wiring diff of `quality-gate.ps1` that deletes a line holding
   `Invoke-Step`. The worker's reading: (c) is small and closes the cheap way out.
4. **`.claude/worktrees/gate` is the lead's own worktree on this machine**
   (`lead/cycle-rereads-queue`). The step's trees would nest inside it: `git add -A` there
   stages `integrate/<cycle>` as an embedded repository, and removing that worktree deletes the
   gate trees under it (recovered now, point 10, at the price of a new environment build).
   Move the worktree, or the step's folder, before scheduling.

5. **The dev stack's database is shared by the gate and the inspectors.** `quality-gate.ps1`
   and the inspectors' integration runs both reset the dev database `pagentos`. Today the lock
   keeps a cycle (and so its inspectors) from starting while a gate runs on this machine; if
   open decision 1 is answered with (b) or (c) - the lock released during the gate - the two
   reset the database under each other (the fourth inspection lost its first probe to exactly
   that: `relation "owner_sessions" does not exist`). It already happens across the two
   machines only if both point at one stack, and with a lead's or an inspector's hand-run
   outside any lock. Options: (a) keep the lock for the whole gate (decision 1a) - simple, and
   a gate pauses the cycle for up to 150 minutes; (b) the gate gets a database of its own
   (`pagentos_gate_<branch>`, created and migrated by the gate's environment step, the name
   passed to the gate) - nothing shared, costs a migration per run and a gate that honours
   the name; (c) a second lock for "whoever resets the dev database", taken by the gate and by
   an inspector's integration run. The worker's reading: (b), and decision 1 only after it.
6. **A run that started nothing is invisible in the Onay Merkezi.** A lock or a Docker stop
   (exit 3, exit 4) is one line in `team/reports/integrate-skipped.log` on this machine and
   nowhere else, on purpose (point 10: it must not replace the branch's last real report). So
   "Docker has been down since the morning and nothing was integrated" cannot be seen from
   the phone. Options: (a) post the newest lines as a report of their own
   (`integrate-skipped.md`) - needs the Onay Merkezi to list it without making it "the newest
   report"; (b) a field in the live status the cycle already PUTs (`integrate: { last_skip,
   since, count }`) shown on the Ofis page - needs the route's schema and the page; (c) after N
   skipped runs in a row, one `awaiting_owner` line ("Docker çalışmıyor, N denemedir") - the
   only one that reaches the owner without being looked for, and the only one that can nag.
   The worker's reading: (b), with (c) for Docker only.

## Consequences

- A dependency reaches main, and its dependants start, without the lead's hands.
- The suite must be added to `scripts/quality-gate.ps1` and `.github/workflows/ci.yml`, and the
  call to `scripts/team/register-nightly.ps1`, by the lead (outside this task's area). The
  scheduled call need not pass the caps (the defaults are caps); it may pass smaller ones.
- Still open after the third round (the inspector's minors): the queue is read before the lock
  is taken and not again after the gate's hour (a stale write is now recovered by the next run,
  not prevented); gate worktrees, each with its `.venv` and `node_modules`, are never removed;
  the `gate-<n>.json` records are local to the machine, so strikes, the moved-ref stop and an
  unapplied verdict are per machine.
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
- Still open after the fifth round: a writer no job object holds (started through a service or
  a scheduled task) that writes WHILE THE GATE RUNS makes the gate judge a tree that is not the
  commit; what reaches main is still exactly the commit (the tree-equality guard), but the
  verdict is then about something else. A file name git quotes (non-ASCII) is refused by the
  allow-list even under `docs/` - the safe direction. The step does not write
  `team/limits.json`, so a limit it met is found again by the next step (one short run).
- Evidence: PROVEN_AUTOMATED with fakes (sandbox repository, fake gate, fake lead, fake API,
  fake docker/uv/pnpm); the process-tree stop is exercised with real Windows processes (a
  child the run leaves behind, and one started through WMI that no job holds). The real gate
  and a real `claude -p` lead run have NOT been run by this task (the fourth inspection ran a
  real lead run and real `uv`/`pnpm` in a scratch clone: PROVEN_PROXY, on the code before this
  round).
