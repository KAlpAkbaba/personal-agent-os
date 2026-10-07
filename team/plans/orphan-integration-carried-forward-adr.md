# ADR draft: an older cycle's integration branch is carried forward, never left behind

Status: proposed (task orphan-integration-carried-forward, cycle d20261006). The lead numbers it.

## Context

Measured 2026-10-07 02:40 (local): five cards had been 'merged' since 2026-10-05, but their commits
were only on integrate/d20261004, 26 commits ahead of main. integrate/d20261005 and the 2026-10-06
releases were cut from main, so that work was never released and nothing reported it. One cost:
dev-db-branch-migration-leak's schema restore never shipped, and proof-from-test-rounds-and-trials
was stopped because of it. Root cause: `Merge-TeamBranch` made `integrate/<cycle>` from main only.

## Decision

1. When `Merge-TeamBranch` creates a cycle's integration branch (that is, `New-TeamWorktree`
   reports `Created`), it first runs `Invoke-TeamCarryForward`. It takes every older
   `integrate/*` branch that a card names in `integration_branch` while the card is still
   'merged', skipping branches whose tip is in main or already in HEAD. Branches go oldest first,
   each with `--no-ff` and the message `carried forward: <branch>`.
   - Carried: the cards' `integration_branch` becomes the new branch, so the integration step
     gates them with it.
   - Conflict, or the branch does not exist: the merge is aborted and the new branch stays where
     it was. The cards become `stopped` with reason `yetim entegrasyon: <branch>` (the
     Danışman's), and a Turkish line names the branch and the cards (`CarriedForward[].Line`).
2. `Get-TeamOrphanMerges` is pure. It returns the 'merged' cards whose integration branch is
   neither the current one nor in main; `Get-TeamBranchesInMain` provides the git answer.
   `New-TeamCycleReport` gains the section "Yetim entegrasyon (yayına ulaşmayan birleşmiş
   işler)" when that list is not empty. By default it reads the repository the lib is in
   (`-RepoRoot` overrides this).
3. `Merge-TeamBranch` takes `-Queue`. Without it, it uses the caller's script-scope `$queue`.
   scripts/team/cycle.ps1 keeps its live queue there and saves the whole document, so the cycle
   carries cards over without changing its call. Callers that have no queue
   (integration-branch.ps1, tests) carry nothing.

## Consequences

- A cycle can begin with older work on its branch. One gate judges it all together, and the
  release takes it.
- A carry-over that conflicts no longer drops cards silently. They are stopped and named, and
  the Danışman resolves them.
- Branches that `scripts/lib/TeamDuty.ps1` (the PM's conflict merge) or
  `scripts/team/integration-branch.ps1` create directly do not carry anything over. In a cycle
  the first approved merge comes through `Merge-TeamBranch`, which creates the branch.
- Suggested follow-up for the lead: pass `-Queue $script:queue` explicitly at cycle.ps1:1998,
  and copy `$merge.CarriedForward[].Line` into the cycle's risks.

## Addendum: merged cards with no integration branch (inspector return, 2026-10-07)

The live queue had five merged cards with an EMPTY `integration_branch`, including
dev-db-branch-migration-leak. The first version left them out. Now:

- `Get-TeamOrphanMerges -HeldIds` counts a merged card with no integration branch as an
  orphan unless its id is in `HeldIds`.
- `Get-TeamUnbranchedHeld` gives `HeldIds`. A card is held when main, or the current
  integration branch, holds its `sha`. Without a sha, its task branch's tip is used.
- A card with neither a sha nor a branch cannot be shown to be in main. It is named.
- The cycle report names such a card as "birleşmiş ama entegrasyon dalı yok, işi (<sha|branch>)
  main'de değil".
- The carry-over skips these cards: there is no branch to merge, and they are not stopped.

Follow-up (not in this card): a card whose sha the newly carried branch holds could take that
branch as its `integration_branch`, so the gate and the release include it.
