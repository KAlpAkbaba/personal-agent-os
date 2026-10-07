# ADR draft: the cycle's area check judges a worker branch on integrate/<cycle> by its own files

- Status: proposed (worker, cycle d20261006, card area-check-wire-integrate-base)
- Context: area-check-integrate-base (8a06a4ee) added `Get-TeamWorkerChangedFiles -AlsoBase` to
  `scripts/lib/TeamArea.ps1`, but `scripts/team/cycle.ps1` (the `worker` arm of the run-result
  switch) still called `Get-TeamChangedFiles -Base $Base`. A worker branch built on
  `integrate/<cycle>` (because the files it tests exist only there) was therefore charged with
  every other card's merge it carries and stopped as "alan dışı dosya" - measured 2026-10-07
  02:52 on two-devices-tests-late-write-routes (3 own files, 5 foreign), the third card of
  that shape.
- Decision: the worker arm calls
  `Get-TeamWorkerChangedFiles -RepoRoot $repoRoot -Branch $branch -Base $Base -AlsoBase "integrate/$CycleId"`.
  The cycle's integration branch is the one `Merge-TeamBranch` writes (`integrate/<cycle-id>`).
  When that branch does not exist or the worker's branch does not contain it, the answer is
  the old `Get-TeamChangedFiles` one, so a branch on main is judged exactly as before.
- Proof: three team-cycle cases (branch on integrate touching only its area -> inspected and
  merged; branch on integrate touching `docs/outside.md` -> stopped with that file alone;
  branch on main beside an integrate branch -> as before). Mutations: back to
  `Get-TeamChangedFiles` (2 RED), wrong AlsoBase name (2 RED), area filter disabled (3 RED).
- Not changed (outside this card's area): `scripts/team/integrate.ps1` ~line 880 still lists a
  task's files with `Get-TeamChangedFiles -Base $baseSha`; it lists files for the integration
  step, not the worker's area check, but a follow-up card should decide whether it needs the
  same `-AlsoBase`.
- Return 1 (inspector, 82224035): the real stopped branch still yields 108 files, because every
  merge moves `integrate/<cycle>` and `Get-TeamWorkerChangedFiles` subtracts the integration
  files only when the branch contains the CURRENT tip (`git merge-base --is-ancestor`,
  `TeamArea.ps1` ~line 591). Proposed rule for the helper: the branch is "on the integration
  branch" when `merge-base(AlsoBase, Branch)` is not already in `Base`; then the worker's files
  are `git diff --name-only <merge-base>..<Branch>` (its own commits only, so an out-of-area file
  is still caught). Two red team-cycle cases (integrate moves after the worker branches) carry it;
  the fix belongs in `scripts/lib/TeamArea.ps1` + `scripts/tests/team-area.tests.ps1` (area
  request), not a second copy of the rule in cycle.ps1 (integrate.ps1 would need the same).
- Return 2 (decision, lead: TeamArea.ps1 and team-area.tests.ps1 added to the area): the helper's
  rule is the one above. `fork = git merge-base refs/heads/<AlsoBase> <Branch>`; no fork (no
  shared history) or `git merge-base --is-ancestor <fork> <Base>` true -> the old
  `<Base>...<Branch>` diff (a branch on main, as before); otherwise `<AlsoBase>...<Branch>`,
  which already starts at the fork, so the branch's own commits only, however far the cycle
  moved the integration tip since. The rule lives in the helper alone, so integrate.ps1 can
  take it later without a copy. Proof: team-area case (h) (integrate tip moves after the
  branch leaves; a second branch with an out-of-area file still shows it) and the two team-cycle
  cases of bebc0579 now green; mutation back to `--is-ancestor <AlsoBase> <Branch>` -> (h) and
  both team-cycle cases RED. Real branch two-devices-tests-late-write-routes (40a9764b) against
  the night's main da3e26b9 with integrate/d20261006 at c3280862: 3 files, all its own (old diff 108).
- Open (separate card, out of this area): trust boundary. Every worktree can write
  `integrate/<cycle>`; a worker that commits an out-of-area file there and builds on it would
  not be charged with it. AlsoBase should be only the integration branch the cycle itself wrote,
  with its recorded sha. Also `integrate.ps1` ~880 (see above).
