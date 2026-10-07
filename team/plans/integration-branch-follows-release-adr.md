# ADR draft: an existing integration branch follows a release on its base

- Card: integration-branch-follows-release (cycle d20261007)
- Status: proposed (the lead numbers it and moves it into docs/DECISIONS.md)

## Context (measured 2026-10-07 11:02, night watch)

- `test-bulgulari-para-defteri-20261007` (inspector APPROVE, sha 21dce840, 3 files all in its
  area) was stopped with "Danisman'a iletildi: entegrasyon cakismasi, ekleme degil:
  scripts/testteam/test-round.ps1" - a file it never touched.
- The release d74a8daa (main, 10:29 local) was built from a separate integration (61ab42e3);
  `git merge-base --is-ancestor d74a8daa integrate/d20261007` fails (local and origin).
- The card's branch was opened from the new lead, so merging it dragged 58 release commits into
  integrate/d20261007; `git merge-tree --write-tree integrate/d20261007 main` alone conflicts in
  scripts/testteam/test-round.ps1 (integrate side 5d04bbac, 1ec424e0; release side 61ab42e3/0dac348b).
- `Merge-TeamBranch` brought the integration branch to -Base only when it created it
  (`$tree.Created`); an existing `integrate/<cycle>` never followed a release.

## Decision

`Merge-TeamBranch` (scripts/lib/TeamRun.ps1), after the "already merged" check and before the
card's merge: when -Base is not an ancestor of the integration branch's HEAD, merge -Base into
it as its own commit, `merge: <Base> into integrate/<cycle> (the release follows)`.

- Clean follow: the card is merged on top as before (`merge: <branch> into integrate/<cycle>`).
- Follow conflicts: `git merge --abort`, the branch tip is unchanged, the card is NOT tried, and
  the answer is `Merged=false, Conflict=true, BaseBehind=true,
  Detail='entegrasyon dali tabanin gerisinde: <unmerged files, comma-separated>'`.
- Nothing new on -Base: no follow commit; the answer is as before, with `BaseBehind=false`.
- A follow that succeeded stays even if the card's own merge then conflicts (the branch is
  correctly at the release; only the card's merge is aborted).

## Caller contract of BaseBehind

`BaseBehind=true` means the conflict is between -Base and the integration branch, not the
card's work: the card must not be returned to its worker or blamed. Today cycle.ps1 and
integration-branch.ps1 read only `Merged` and still record "entegrasyon dalında çakışma" and
return the card; making cycle.ps1 hand a BaseBehind answer to the Danışman (resolve_integration)
with the Detail text, without counting a return against the card, is a follow-up card
(cycle.ps1 is outside this card's area).

## Consequences / risks

- `Get-TeamGuardLastMerge` (TeamGuards.ps1) matches `^merge: (team/\S+) into`: with -Base `main`
  the follow commit is skipped; a -Base starting with `team/` (e.g. tick -Base team/nightly/lead)
  would be read as the "last merged card". Follow-up if such a base is used.

## Evidence

scripts/tests/team-integrate.tests.ps1 "follow (1)/(1b)/(2)/(3)": RED before (4 FAIL), GREEN
after; mutation (follow disabled) -> 3 FAIL, restored sha256 equal; full file 107/107.
