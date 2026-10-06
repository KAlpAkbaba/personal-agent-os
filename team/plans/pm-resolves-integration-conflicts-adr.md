# ADR draft: the Proje Yöneticisi resolves integration-branch conflicts itself

Task: pm-resolves-integration-conflicts (cycle d20261006). Replaces lead.md duty rule (d) for
integration conflicts (ADR-0283 escalated every one to the Danışman).

## Context

The owner, 2026-10-06: "çalışan 2'nin direk sana değil proje yöneticisine gitmeli". On
2026-10-05/06 six approved tasks (mail-accounts-connect, conversation-transcripts,
home-stock-list, run-temp-keeps-git-bash-tmp, pm-stuck-run-check, migration-revision-from-tree)
sat "Danışman'da" for hours with `entegrasyon dalında çakışma`. Every one was mechanical: both
sides' lines kept, an alembic down_revision re-pointed at the chain tip, a guards.json entry
kept twice.

## Decision

A new duty action `resolve_integration`, carried out by a deterministic script
(`scripts/lib/TeamDuty.ps1`, `Invoke-TeamDutyResolveIntegration`), not by the duty LLM run
(which has no shell):

1. The task branch is merged into a SCRATCH worktree detached at the integration tip, in
   diff3 conflict style. The integration worktree is not touched until the end.
2. A conflict hunk is resolved only when it is additive: for some split k of the base lines,
   each side is base[0..k) + its own insertion + base[k..); the union is base[0..k) + ours +
   theirs (once when equal) + base[k..). The latest k is tried first (insertions after the base
   lines). In `.json` a trailing comma is not a change and the comma between the two
   insertions is written; the file must parse afterwards.
3. When the merged alembic chain has more than one head, the task's newly added migrations
   (one chain) are renumbered after the integration head (`NNNN_slug` -> next number), the
   file renamed, `revision`, `down_revision`, the docstring's `Revision ID` / `Revises`, and
   references in the task's own changed files updated; exactly one head is then required.
4. The guards of `team/guards.json` plus the task's own test files (`scripts/tests/*.tests.ps1`,
   `services/api/tests/**/test_*.py`) run on the merged tree (Invoke-TeamGuards).
5. Only when all of that is green is `integrate/<cycle>` fast-forwarded to the merge commit
   (its second parent is the task tip, so a later Merge-TeamBranch reads it as merged), and the
   task set `merged`.

Escalation to the Danışman (reason `Danışman'a iletildi: entegrasyon çakışması, <case>: ...`)
only for: `korunan dosya` (Get-TeamAreaProtection on a conflicted path), `ekleme değil` (a base
line deleted or rewritten, a modify/delete, or an add/add - both sides wrote the same NEW file
with different text: with no base every pair of texts looks like two insertions, and the first
draft glued the two whole files together and merged them), `koruyucu kırmızı` (a guard of
team/guards.json, or the task's own test, red on the merged tree), `iki çözüm denemesi başarısız`. A first mechanical failure is counted in the
reason (`... (1/2): ...`) and the task stays the PM's. In every outcome but merged the
integration branch is byte-identical to before. While working, the task's reason starts
`Proje Yöneticisi çözüyor: ` (the Ofis label).

## Consequences

- Wiring still needed (outside this card's area, requested): `Test-TeamDuty` must accept
  `resolve_integration` (scripts/lib/TeamQueue.ps1 `$script:TeamDutyActions`), cycle.ps1 must
  call Invoke-TeamDutyResolveIntegration for that decision, the Ofis model
  (apps/web/app/core/office/officeModel.ts) must map `Proje Yöneticisi çözüyor:` to its label,
  and the new suite needs its line in `.github/workflows/ci.yml` (the ci-covers-every-suite
  guard reads ci.yml) and in scripts/quality-gate.ps1. Merged without that wiring, the suite's
  Test-TeamDuty case and the ci-covers-every-suite guard are red on the integration tree - and
  that guard is in team/guards.json, so every resolution would escalate `koruyucu kırmızı`.
  The branch and its wiring must land together.
- Follow-up card: `migration-rechain-on-merge` (same cycle) adds TeamMigrationChain.ps1 to
  Merge-TeamBranch for clean merges; once both are in, Update-TeamDutyMigrationChain should call
  that plan instead of keeping its own renumbering.
- `.claude/agents/lead.md` could not be written from the worker run (the harness refused the
  write to `.claude/agents`); the text below replaces rule (d) and is the lead's to apply.
- A semantic conflict that is textually additive (two functions of the same name appended) is
  merged; the guards and the task's tests are the net, and the full gate still runs after.

## Proposed lead.md text (replaces rule (d) of "Nöbet: duran işler")

```
- (d) a conflict on the integration branch (`entegrasyon dalında çakışma`) is YOURS to resolve,
  not the Danışman's (the owner, 2026-10-06: "çalışan 2'nin direk sana değil proje yöneticisine
  gitmeli") -> `resolve_integration`. The cycle then runs `scripts/lib/TeamDuty.ps1`
  (Invoke-TeamDutyResolveIntegration) in a scratch worktree of `integrate/<cycle>`: it merges
  the task branch, keeps both sides where they are additive, renames a new alembic migration
  and re-points its revision and down_revision after the current head (one head, proven), runs
  the guards of `team/guards.json` and the task's own tests, and only when all are green moves
  `integrate/<cycle>` and sets the task merged. The Ofis shows it as "Proje Yöneticisi çözüyor".
  It goes to the Danışman by itself ONLY when: a resolution would delete or rewrite the other
  side's lines or both sides wrote the same new file (`ekleme değil`), a protected file (`.claude/agents`, the constitution, CLAUDE.md,
  secrets, LKG, the recovery roots) is in the conflict (`korunan dosya`), a guard stays red on
  the merged tree (`koruyucu kırmızı`), or two resolutions in a row failed (`iki çözüm denemesi
  başarısız`); the reason line names which. In each of these the integration branch is unchanged;
- (e) a lead-protected file (the shared files, `.claude/agents`, the constitution, CLAUDE.md,
  secrets, LKG, the recovery roots) outside an integration conflict, a security or architecture
  decision, an owner rule, a release or host step, or a task the owner or the Danışman stopped by
  hand -> `escalate`: the Danışman decides; your reason says what to decide.
```

and in the decision-file line: `"action": "return" | "grant_and_return" | "resolve_integration" | "escalate"`.
Apply it together with the Test-TeamDuty wiring: before that, a decision file naming
`resolve_integration` is refused whole.
