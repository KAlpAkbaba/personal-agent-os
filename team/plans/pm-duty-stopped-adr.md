# ADR (draft, unnumbered): the Proje Yöneticisi's duty run for stopped tasks (pm-duty-stopped)

**Date.** 2026-10-03. **Status.** Proposed by the worker; the Danışman numbers it.

## Context

The owner, 2026-10-03: "Böyle bulgular bulunduğunda konuyu proje yöneticisine iletsinler, proje
yöneticisi de sana iletsin; her seferinde bu süreci ben takip etmeyeyim." and "Proje Yöneticisi
koltuğu var zaten, sadece rolü ve şemayı üzerine alması gerekmez mi?"

A task the cycle STOPS (two inspector returns, `alan dışı dosya`, `entegrasyon dalında çakışma`,
two failed runs) waited until a person saw it on the Ofis page. The cycle had a lead seat
(`$seats.lead = 1`) but only ever started a lead run for the split of an approved idea.

## Decision

1. **Trigger.** At a refill, when the lead seat is free and `-NoDuty` was not given, the stopped
   tasks this cycle has not handed at their current stop (`Get-TeamDutyCandidates`: id + the
   `updated_at` it had when handed) start ONE lead run (`lead.md`, Bash and Edit excluded:
   Read/Grep/Glob/Write). At most 8 tasks a run; the rest go to the next run. The duty comes before
   a split in the one lead seat; the two never run at once. Set aside: a task whose reason starts
   with `Danışman'a iletildi: ` (the Danışman has it - also across cycles), a task whose write the
   store refused, one the cycle abandoned, one that had `-MaxRunsPerTask` runs (its worker cannot
   run again in this cycle - handing it would loop), one whose return waits for another task's
   files, and one already handed 3 times in this cycle (a hang guard on paid runs; said once under
   the risks). A handed task that leaves `stopped` loses its entry, so its next stop is new even
   inside the same second.
2. **The card** (`New-TeamDutyCard`, TeamRun.ps1): per task id, title, area, depends_on, branch,
   sha, returns, failed_runs, the stop reason, its last three report paths; and
   `- duty_file: team/plans/<cycle>-duty-<n>.json` (the first free n).
3. **The file and its judge** (`Read-TeamDutyFile`, `Test-TeamDuty`, TeamQueue.ps1):
   `{ "decisions": [ { task, action, grant, reason } ] }`. Every task one of the listed ones, once;
   action `return | grant_and_return | escalate`; reason non-blank, <= 1200 characters; grant only
   with `grant_and_return`, a LIST of 1..5 plainly written repository-relative paths
   (`ConvertTo-TeamAreaPath`), none lead-protected (`Get-TeamAreaProtection`: TeamArea.ps1's one
   list), the area <= 25 entries. Any problem refuses the WHOLE file: nothing changes, and the
   report's risks say `nöbet kararı reddedildi (duty-n): ...`. Without TeamArea.ps1 loaded the
   judge refuses every file (fails closed); cycle.ps1 dot-sources it when it is there.
4. **Apply** (`Complete-Duty`, `Invoke-DutyDecision`): `return` -> `returned`, reason
   `Proje Yöneticisi: <reason>`; `grant_and_return` -> the area gains the paths, then the same;
   `escalate` -> stays `stopped`, reason `Danışman'a iletildi: <reason>`, and a risks line. A task
   that changed while the run worked (not stopped any more, another `updated_at`, or moved in the
   store) is left alone and said. A return the protocol refuses (`Get-TeamAreaHolders`, then
   `Test-TeamQueue` on a trial queue) is not forced: the task stays stopped with
   `Proje Yöneticisi: <reason> (alan çakışması: <task>; o iş bitince)`; when the holder leaves the
   work THIS script makes the return (`Resolve-DutyWaits`, at each refill). Every write goes
   through `Save-PoolQueue` - the cycle's own path, API mode included.
5. **The role text**: `.claude/agents/lead.md` gains "Nöbet: duran işler (Proje Yöneticisi)" -
   (a) outside the area -> grant_and_return, (b) clear findings -> return with an explicit list,
   (c) the third return -> change the approach, (d) integration conflict, protected file,
   security/architecture, owner rule, release/host step, a hand-stopped task -> escalate. The
   frontmatter is unchanged (pinned by test_team_guards_runner.py).

## Consequences

- The owner is no longer the one who notices stopped work; the Danışman sees only escalations.
- On the first cycle after the merge the store's historical stopped tasks are handed too, 8 a
  run, one run after the other while the lead seat is free; escalate the ones nobody should touch
  (or run a cycle with `-NoDuty`) if that is not wanted.
- A return the Proje Yöneticisi makes does not reset `returns`: the next inspector RETURN stops the
  task again and hands it back (the "third return" of the role text).
- Tests: scripts/tests/team-cycle.tests.ps1 (unit cases for the candidates, the file, the judge,
  the card, fails-closed; cycle cases with the fake for return, grant, a protected grant refusing
  the whole file, escalate, overlap, stopped again, none, no file, the split beside it, API mode).
  The harness passes `-NoDuty` unless a case asks for the duty, so the runs other cases count are
  unchanged.
