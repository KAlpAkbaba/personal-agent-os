# ADR draft: the fast guards run after the merge, before the gate (card guards-after-every-merge)

Status: proposed (worker-3, cycle d20261006). The lead numbers it.

## Context

The owner, 2026-10-06: "bu kapının daha hızlı kontrolünü sağlamanın bir yolu var mı, çok zaman
kaybediyoruz". Most reds that restarted the 95-minute gate that day were cross-cutting guards that
run in seconds (a suite missing from the CI lists, a capability family with no Turkish name, a loop
missing from health, staging isolation, the Postgres coverage baseline, ruff line length). Each only
fails once two cards meet on the integration branch, and the gate found it 40-60 minutes in.

## Decision

1. `team/guards.json` is the list of these guards. Added: `capability-family-names`
   (test_capability_list.py), `loops-in-health` (test_bounded_delivery.py, "every background loop
   the app starts can be seen in health"), `staging-isolation` (test_staging_isolation.py).
   `migration-revision-literals` is already on integrate/d20261006 and is not repeated here.
2. `scripts/team/guards.ps1 -AfterMerge` runs the list plus `ruff check .` in services/api (a row
   the step adds itself; the LIST keeps its two kinds) and names the task branch merged last
   (`Get-TeamGuardLastMerge`: the newest first-parent "merge: team/... into ..." commit, never
   main's merge nor the lead's wiring commit). Exit 0/1/2 as before; the result gets `last_merge`;
   exit 2 also writes `{status: cannot_run, reason}` to -OutFile (the caller reads the file in
   UTF-8, never the console in another code page).
3. `scripts/team/integrate.ps1` step 4b: after the lead's wiring is committed and the integration
   branch moved to it, and BEFORE the gate, it runs (2) on the gate tree (the tree's own .venv when
   it has one; cap -GuardMinutes, 20). Green: one report line and the gate runs. Red: the gate is
   NOT started; the merge stays on the branch; the task merged last is named with the guard and
   becomes `returned`; the attempt is a `red` gate record with `by: "guards"` - so the existing
   rules hold unchanged (the branch waits for the returned task; the same commit is not tried
   again; two red attempts stop the branch). No list, or a runner that could not run: said, and the
   gate (which judges everything anyway) runs.
4. The gate itself is unchanged.

## Measured

On this repository (worktree of the card, 2026-10-06): 11 rows, 57 s in total (slowest:
script-syntax 10 s, the pytest guards 3-8 s each, ruff 3 s). Target was under 3 minutes.

## Not done here (outside the card's area)

- The cycle's own merge (`scripts/team/cycle.ps1` after `Merge-TeamBranch`) and the Proje
  Yöneticisi's conflict resolution (TeamDuty.ps1 on branch worker-pm-resolves-integration-conflicts)
  do not call the runner yet. Each needs one call: `guards.ps1 -Worktree <integrate tree>
  -AfterMerge -OutFile <file>` after its merge, reporting the red row and the card (the merge
  stays). Proposed as a follow-up card; the integrate step already catches it before any gate.
- On a task branch `ci-covers-every-suite` is red by nature until the lead wires a new suite.
  On the integration branch the step runs AFTER the lead's wiring, so it judges the wired tree.

## Consequences

A red guard costs about a minute instead of the gate's hour and a half, and names one card. The
blame is "merged last", not a bisection: when two cards only break together, the last one goes back
- the cheaper of the two to re-merge, and the rule the owner asked for.
