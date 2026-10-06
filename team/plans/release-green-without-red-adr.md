# ADR draft — release-green-without-red: a red card does not hold the green ones

Status: proposed (worker, cycle d20261005). The lead numbers it (an addendum to ADR-0214 / ADR-0287).

## Context

The owner, 2026-10-06: "testler yeşil olanları otomatik yayına alsak". `scripts/team/integrate.ps1`
gated the integration branch whole. When the gate was red and named one task's files, it wrote
"dal ... bütün olarak girer: X düzeltilip yeniden birleşene kadar hiçbiri kapıya girmez". Every green
card waited for the one red card, and `release.ps1` (ADR-0287) never got a green record to release.

## Decision

When the full gate is red, blames one or more tasks, at least one other merged task is on the branch,
and this red does not stop the branch (TEAM_PROTOCOL 10: two strikes, the lead looks first):

1. The blamed tasks are returned as before (their reason carries the gate's words).
2. In the gate worktree the branch is rebuilt from the base the gate ran on
   (`New-TeamRebuiltBranch`, scripts/lib/TeamIntegrate.ps1): the first-parent commits of the gated
   commit, in their order. A merge whose second parent is already in the line (main merged in
   before the gate) is skipped. A merge that carries a blamed task's code (its own merge, or a branch
   built on top of it) is dropped. A merge or the lead's wiring commit that does not apply without
   what was dropped is aborted and dropped. Every drop is named in the report; a task dropped
   this way stays `merged` and says it waits with the blamed one.
3. The result is kept as `integrate/<cycle>-kalan-<n>` (a new ref, created with a must-not-exist
   update-ref; the integration branch itself is never moved backwards) and gated ONCE more in the
   same run. The environment is built again only if what was left out changed one of its files.
4. Green: the remaining tasks go onto main exactly as a green integration branch does
   (`Complete-GreenGate`, factored out of the old tail), and the green record
   (`team/reports/<cycle>/gate-<n>.json`, `main` = the merge, `branch` = the rebuilt branch,
   `rebuilt_from`, `left_out`) is what `release.ps1` finds. Migration commits still wait for the
   Danışman under release.ps1's own rules. Every task's reason (the Onay Merkezi line) says
   "kırmızı iş ayrıldı: X; kalanlar yeniden kapıda (<rebuilt branch>)".
5. Red: no third gate. Everything waits; the report and the remaining tasks carry
   "Danışman'a: ... kalanlar da kırmızı; üçüncü kapı koşmadı, hepsi bekliyor". The rebuilt gate's
   record is on the rebuilt branch's name, so it is no strike against the integration branch.

A red gate that blames nobody, a branch that holds only the blamed tasks, and a red that stops the
branch keep today's behaviour.

## Consequences

- One red card costs at most one more full gate per run (the gate is ~1 hour; the lock is held).
- The integration branch stays held by the returned task. When its fix is merged, the whole branch
  is gated again: the tasks already on main merge cleanly (the same content), and the rest go out.
- Risk: if the queue write fails after a green rebuilt gate moved main, the next run applies the
  integration branch's red verdict (unapplied) and the passed tasks stay `merged` until the blamed
  one is fixed - their code is on main but they are released one round later. Not lost.
- Risk: the lead's wiring for the blamed task is replayed when it applies; if it references the
  blamed task's files the rebuilt gate is red and everything waits (safe direction).
